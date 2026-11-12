"""Seat layout use cases: the zones of an event and the seats inside them.

Split from EventService because the spec treats them as separate concerns with
separate preconditions - an event's own details stay editable after publishing,
while its layout freezes. Both go through the same aggregate: zones and seats
belong to the Event aggregate, so they share its transaction and its row lock.
"""
import logging
from typing import TYPE_CHECKING

from app.cache.event_detail import EventDetailCache
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
if TYPE_CHECKING:
    from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)


class SeatLayoutService:
    """Orchestrates the zone and seat layout of an event."""

    def __init__(
        self,
        event_repository: EventRepository,
        event_rule_validator: EventRuleValidator,
        event_schema_mapper: EventSchemaMapper,
        event_detail_cache: EventDetailCache,
    ):
        self._repository = event_repository
        self._validator = event_rule_validator
        self._mapper = event_schema_mapper
        self._event_detail_cache = event_detail_cache

    async def create_event_zone(
        self, event_id: int, payload: EventZoneCreate
    ) -> EventZoneResponse:
        async with self._repository.load_for_update(event_id) as event_aggr:
            self._validator.validate_unpublished(event_aggr.state)
            created = await event_aggr.add_zone(payload)
            return self._mapper.to_zone(created)

    async def update_event_zone_profile(
        self,
        event_id: int,
        event_zone_id: int,
        payload: EventZoneProfileUpdate,
        background_tasks: "BackgroundTasks",
    ) -> EventZoneResponse:
        """Updates a zone's name and ticket price.

        Deliberately has no unpublished check: name and price stay editable
        after the event goes live.
        """
        async with self._repository.load_for_update(event_id) as event_aggr:
            updated = await event_aggr.update_zone_profile(event_zone_id, payload)
            is_published = event_aggr.state.is_active
        if is_published:
            background_tasks.add_task(self._rebuild_event_detail_cache, event_id)
        return self._mapper.to_zone(updated)

    async def _rebuild_event_detail_cache(self, event_id: int) -> None:
        async with self._repository.load(event_id) as event_aggr:
            detail = self._mapper.to_event_detail(await event_aggr.layout())
        await self._event_detail_cache.sync(detail)

    async def list_event_zone_seats(
        self, event_id: int, event_zone_id: int, pagination: PaginationParams
    ) -> Page[EventSeatResponse]:
        """One page of a published event's zone layout with each seat's current status."""
        async with self._repository.load(event_id) as event_aggr:
            self._validator.validate_publicly_visible(event_aggr.state)
            page = await event_aggr.zone_seats(event_zone_id, pagination)
            return self._mapper.to_seat_page(page)

    async def list_event_zone_seats_for_manager(
        self, event_id: int, event_zone_id: int, pagination: PaginationParams
    ) -> Page[EventSeatResponse]:
        async with self._repository.load(event_id) as event_aggr:
            page = await event_aggr.zone_seats(event_zone_id, pagination)
            return self._mapper.to_seat_page(page)

    async def replace_event_zone_seats(
        self, event_id: int, event_zone_id: int, payload: EventSeatLayoutUpdate
    ) -> EventSeatLayoutSyncResponse:
        """The occupied-seat check and the rewrite share one locked transaction, so
        a seat cannot be claimed in between and then silently dropped.
        """
        async with self._repository.load_for_update(event_id) as event_aggr:
            self._validator.validate_unpublished(event_aggr.state)
            self._validator.validate_no_occupied_seat(
                await event_aggr.occupied_seat_count(event_zone_id)
            )
            counts = await event_aggr.sync_zone_seats(event_zone_id, payload.seats)
            return self._mapper.to_layout_sync(counts)
