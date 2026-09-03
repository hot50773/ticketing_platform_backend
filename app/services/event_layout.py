"""Event layout use cases: the zones of an event and the seats inside them.

Split from EventService because the spec treats them as separate concerns with
separate preconditions - an event's own details stay editable after publishing,
while its layout freezes. Both go through the same aggregate: zones and seats
belong to the Event aggregate, so they share its transaction and its row lock.
"""
import logging

from app.repositories.event import EventRepository
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    EventSeatLayoutSyncResponse,
    EventSeatLayoutUpdate,
    EventSeatResponse,
    EventZoneCreate,
    EventZoneProfileUpdate,
    EventZoneResponse,
)
from app.services.mappers import EventSchemaMapper
from app.services.validators import EventRuleValidator

logger = logging.getLogger(__name__)


class EventLayoutService:
    """Orchestrates the zone and seat layout of an event."""

    def __init__(
        self,
        event_repository: EventRepository,
        event_rule_validator: EventRuleValidator,
        event_schema_mapper: EventSchemaMapper,
    ):
        self._repository = event_repository
        self._validator = event_rule_validator
        self._mapper = event_schema_mapper

    async def create_event_zone(
        self, event_id: int, payload: EventZoneCreate
    ) -> EventZoneResponse:
        """flow: assert unpublished -> create the zone and its seats."""
        async with self._repository.load_for_update(event_id) as event:
            self._validator.validate_unpublished(event.status)
            created = await event.add_zone(payload)
            return self._mapper.to_zone(created)

    async def update_event_zone_profile(
        self, event_id: int, event_zone_id: int, payload: EventZoneProfileUpdate
    ) -> EventZoneResponse:
        """Updates a zone's name and ticket price.

        Deliberately has no unpublished check: name and price stay editable
        after the event goes live.
        """
        async with self._repository.load_for_update(event_id) as event:
            updated = await event.update_zone_profile(event_zone_id, payload)
            return self._mapper.to_zone(updated)

    async def list_event_zone_seats(
        self, event_id: int, event_zone_id: int, pagination: PaginationParams
    ) -> Page[EventSeatResponse]:
        """One page of a zone's seat layout with each seat's current status."""
        async with self._repository.load(event_id) as event:
            page = await event.zone_seats(event_zone_id, pagination)
            return self._mapper.to_seat_page(page)

    async def replace_event_zone_seats(
        self, event_id: int, event_zone_id: int, payload: EventSeatLayoutUpdate
    ) -> EventSeatLayoutSyncResponse:
        """flow: assert unpublished -> assert no occupied seat -> sync the layout.

        The occupied-seat check and the rewrite share one locked transaction, so
        a seat cannot be claimed in between and then silently dropped.
        """
        async with self._repository.load_for_update(event_id) as event:
            self._validator.validate_unpublished(event.status)
            self._validator.validate_no_occupied_seat(
                await event.occupied_seat_count(event_zone_id)
            )
            counts = await event.sync_zone_seats(event_zone_id, payload.seats)
            return self._mapper.to_layout_sync(counts)
