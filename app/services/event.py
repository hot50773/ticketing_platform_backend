"""Event lifecycle use cases: create, read, update, publish, delete.

Every use case enters the repository exactly once, through an aggregate that
owns a single transaction. Reading state and writing the decision that depends
on it therefore happen together; see app/aggregates/event.py for why the write
paths also take a row lock.

Boundaries:
  - persistence goes through the aggregate, never a session
  - domain rules go through EventRuleValidator, which stays I/O free
  - ORM -> schema translation goes through EventSchemaMapper
  - the zone / seat layout lives in EventLayoutService, not here
  - nothing here touches Redis; warming the seat bitmap belongs to
    publish_event, where the layout freezes and serial_number becomes a
    stable offset
"""
import logging

from app.cache.event_layout import EventLayoutCache
from app.models import EventZone
from app.repositories.event import EventRepository
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    EventCreate,
    EventDetailResponse,
    EventHoldingTimeUpdate,
    EventProfileUpdate,
    EventResponse,
    EventZoneInternal,
    EventLayoutInternal,
    EventZoneWithSeatInternal,
)
from app.services.mappers import EventSchemaMapper
from app.services.validators import EventRuleValidator

logger = logging.getLogger(__name__)


class EventService:
    """Orchestrates the lifecycle of an event."""

    def __init__(
        self,
        event_repository: EventRepository,
        event_layout_cache: EventLayoutCache,
        event_rule_validator: EventRuleValidator,
        event_schema_mapper: EventSchemaMapper,
    ):
        self._repository = event_repository
        self._validator = event_rule_validator
        self._mapper = event_schema_mapper
        self._cache = event_layout_cache

    async def create_event(self, payload: EventCreate) -> EventDetailResponse:
        """flow: validate holding time -> create event + clone venue layout.

        The event is always created unpublished with every zone price unset.
        """
        self._validator.validate_holding_time(payload)
        async with self._repository.create(payload) as event:
            return self._mapper.to_event_detail(await event.layout())

    async def list_events(self, pagination: PaginationParams) -> Page[EventResponse]:
        """One page of non-deleted events."""
        page = await self._repository.get_events(pagination)
        return self._mapper.to_event_page(page)

    async def get_event(self, event_id: int) -> EventDetailResponse:
        """Returns an event with its zones (seat counts only, not the seats)."""
        async with self._repository.load(event_id) as event:
            return self._mapper.to_event_detail(await event.layout())

    async def update_event_profile(
        self, event_id: int, payload: EventProfileUpdate
    ) -> EventResponse:
        """Updates name / description. Allowed while the event is published."""
        async with self._repository.load_for_update(event_id) as event:
            await event.update_profile(payload)
            return self._mapper.to_event(event.event)

    async def update_event_holding_time(
        self, event_id: int, payload: EventHoldingTimeUpdate
    ) -> EventResponse:
        """flow: assert unpublished -> validate start_at / end_at -> update.

        Publication is checked before the dates so a published event is told to
        unpublish first, rather than being sent to fix dates it cannot change.
        """
        async with self._repository.load_for_update(event_id) as event:
            self._validator.validate_unpublished(event.status)
            self._validator.validate_holding_time(payload)
            await event.set_holding_time(payload)
            return self._mapper.to_event(event.event)

    async def publish_event(self, event_id: int) -> EventResponse:
        """flow: assert every zone exists and is priced -> put on sale.

        Publishing and unpublishing share nothing but the column they write:
        different preconditions, different failure modes, and publishing is
        where the layout freezes. They are therefore separate use cases.
        """
        async with self._repository.load_for_update(event_id) as event_aggr:
            event_layout = await event_aggr.event_layout()
            self._validator.validate_publishable(event_layout.zones)

            await self._cache.init_event_layouts(event_layout)
            await event_aggr.publish()

            return self._mapper.to_event(event_aggr.event)

    async def unpublish_event(self, event_id: int) -> EventResponse:
        """flow: assert no live order -> take off sale."""
        async with self._repository.load_for_update(event_id) as event:
            self._validator.validate_no_live_order(await event.order_stats())
            await event.unpublish()
            return self._mapper.to_event(event.event)

    async def delete_event(self, event_id: int) -> None:
        """flow: assert no live order -> soft-delete event + zones + seats."""
        async with self._repository.load_for_update(event_id) as event:
            self._validator.validate_no_live_order(await event.order_stats())
            await event.soft_delete()
