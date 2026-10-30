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
  - Redis is touched only where the layout freezes: publish_event warms the
    seat bitmaps, unpublish_event and delete_event drop them again
"""
import logging

from app.cache.event_layout import EventLayoutCache
from app.repositories.event import EventRepository
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    EventCreate,
    EventDetailResponse,
    EventHoldingTimeUpdate,
    EventProfileUpdate,
    EventResponse,
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
        """The event is always created unpublished with every zone price unset."""
        self._validator.validate_holding_time(payload)
        async with self._repository.create(payload) as event_aggr:
            return self._mapper.to_event_detail(await event_aggr.layout())

    async def list_events(self, pagination: PaginationParams) -> Page[EventResponse]:
        """One page of non-deleted events."""
        paged_list = await self._repository.get_events(pagination)
        return self._mapper.to_event_page(paged_list)

    async def get_event(self, event_id: int) -> EventDetailResponse:
        """Returns an event with its zones (seat counts only, not the seats)."""
        async with self._repository.load(event_id) as event_aggr:
            return self._mapper.to_event_detail(await event_aggr.layout())

    async def update_event_profile(
        self, event_id: int, payload: EventProfileUpdate
    ) -> EventResponse:
        """Updates name / description. Allowed while the event is published."""
        async with self._repository.load_for_update(event_id) as event_aggr:
            await event_aggr.update_profile(payload)
            return self._mapper.to_event(event_aggr.event)

    async def update_event_holding_time(
        self, event_id: int, payload: EventHoldingTimeUpdate
    ) -> EventResponse:
        """Publication is checked before the dates so a published event is told to
        unpublish first, rather than being sent to fix dates it cannot change.
        """
        async with self._repository.load_for_update(event_id) as event_aggr:
            self._validator.validate_unpublished(event_aggr.state)
            self._validator.validate_holding_time(payload)
            await event_aggr.set_holding_time(payload)
            return self._mapper.to_event(event_aggr.event)

    async def publish_event(self, event_id: int) -> EventResponse:
        """Publishing and unpublishing share nothing but the column they write:
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
        """The cache is cleared *after* the transaction commits, the mirror image
        of publish_event. Dropping a still-live event's bitmap would make every
        seat read as non-existent, so it must not happen until the event is
        certainly off sale; a bitmap left behind by a failed clear is inert.
        """
        async with self._repository.load_for_update(event_id) as event_aggr:
            self._validator.validate_no_active_order(await event_aggr.order_stats())
            zone_ids = await event_aggr.zone_ids()
            await event_aggr.unpublish()
            response = self._mapper.to_event(event_aggr.event)

        await self._cache.clear_event_layouts(event_id, zone_ids)
        return response

    async def delete_event(self, event_id: int) -> None:
        """The bitmaps are cleared after the commit, as in unpublish_event.

        The delete hands back the zone ids it removed; querying for them
        afterwards would come back empty.
        """
        async with self._repository.load_for_update(event_id) as event_aggr:
            self._validator.validate_no_active_order(await event_aggr.order_stats())
            zone_ids = await event_aggr.soft_delete_cascade()

        await self._cache.clear_event_layouts(event_id, zone_ids)
