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
    update_event_status(publish), where the layout freezes and serial_number
    becomes a stable offset
"""
import logging

from app.repositories.event import EventRepository
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    EventCreate,
    EventDetailResponse,
    EventHoldingTimeUpdate,
    EventProfileUpdate,
    EventResponse,
    EventStatusUpdate,
)
from app.services.mappers import EventSchemaMapper
from app.services.validators import EventRuleValidator

logger = logging.getLogger(__name__)


class EventService:
    """Orchestrates the lifecycle of an event."""

    def __init__(
        self,
        event_repository: EventRepository,
        event_rule_validator: EventRuleValidator,
        event_schema_mapper: EventSchemaMapper,
    ):
        self._repository = event_repository
        self._validator = event_rule_validator
        self._mapper = event_schema_mapper

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

    async def update_event_status(
        self, event_id: int, payload: EventStatusUpdate
    ) -> EventResponse:
        """flow: publish   -> assert every zone has a ticket price -> update
                 unpublish -> assert no live order                 -> update
        """
        async with self._repository.load_for_update(event_id) as event:
            if payload.is_active:
                self._validator.validate_publishable(await event.zone_pricings())
            else:
                self._validator.validate_no_live_order(await event.order_stats())
            await event.set_status(payload.is_active)
            return self._mapper.to_event(event.event)

    async def delete_event(self, event_id: int) -> None:
        """flow: assert no live order -> soft-delete event + zones + seats."""
        async with self._repository.load_for_update(event_id) as event:
            self._validator.validate_no_live_order(await event.order_stats())
            await event.soft_delete()
