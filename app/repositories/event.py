"""Persistence for the Event aggregate (Event / EventZone / EventSeat).

Every public method owns its own transaction. Soft-deleted rows
(`deleted_at IS NOT NULL`) are excluded from every read.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Event, EventSeat, EventZone
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    EventCreate,
    EventHoldingTimeUpdate,
    EventLayoutInternal,
    EventOrderStatsInternal,
    EventProfileUpdate,
    EventSeatSyncCountsInternal,
    EventSeatLayout,
    EventZoneCreate,
    EventZonePricingInternal,
    EventZoneProfileUpdate,
    EventZoneWithSeatCountInternal,
)


class EventRepository:
    """Repository managing Event aggregate database transactions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    # ----------------------------------------------------------------------- #
    # Event
    # ----------------------------------------------------------------------- #
    async def create_event_from_venue_template(self, payload: EventCreate) -> EventLayoutInternal:
        """Creates an unpublished event and clones the venue's layout into
        EventZone/EventSeat inside a single transaction.

        EventSeat.serial_number is reassigned contiguously from 1 per zone
        (it is the Redis bitmap offset), NOT copied from Seat.serial_number.
        Cloning uses `INSERT ... SELECT` rather than ORM row-by-row inserts: a
        10k-seat venue would otherwise round-trip 10k objects through Python.
        Reading zone/seat here crosses aggregates on purpose - it is the only
        way to keep the copy atomic and cheap.

        Raises EntityNotFoundError when the venue does not exist or is deleted.
        """
        raise NotImplementedError

    async def get_events(self, pagination: PaginationParams) -> Page[Event]:
        """One page of non-deleted events ordered by id, plus the total count."""
        raise NotImplementedError

    async def get_and_check_event(self, event_id: int) -> Event:
        """Fetches a non-deleted event. Raises EntityNotFoundError if absent."""
        raise NotImplementedError

    async def get_event_layout(self, event_id: int) -> EventLayoutInternal:
        """Fetches an event with its zones and per-zone seat counts."""
        raise NotImplementedError

    async def update_event_profile(self, event_id: int, payload: EventProfileUpdate) -> Event:
        """Updates name and description."""
        raise NotImplementedError

    async def update_event_holding_time(
        self, event_id: int, payload: EventHoldingTimeUpdate
    ) -> Event:
        """Updates start_at and end_at."""
        raise NotImplementedError

    async def update_event_status(self, event_id: int, is_active: bool) -> Event:
        """Publishes or unpublishes the event."""
        raise NotImplementedError

    async def soft_delete_event_cascade(self, event_id: int) -> None:
        """Soft-deletes the event together with its zones and seats in one transaction."""
        raise NotImplementedError

    # ----------------------------------------------------------------------- #
    # EventZone / EventSeat
    # ----------------------------------------------------------------------- #
    async def create_event_zone_with_seats(
        self, event_id: int, payload: EventZoneCreate
    ) -> EventZoneWithSeatCountInternal:
        """Creates a zone and its seats atomically, numbering the seats 1..N
        in `(row, col)` order. Raises EntityConflictError on a duplicate zone name.
        """
        raise NotImplementedError

    async def get_and_check_event_zone(self, event_id: int, event_zone_id: int) -> EventZone:
        """Fetches a zone and asserts it belongs to the given event."""
        raise NotImplementedError

    async def get_event_zone_seats(
        self, event_zone_id: int, pagination: PaginationParams
    ) -> Page[EventSeat]:
        """One page of a zone's non-deleted seats ordered by serial_number."""
        raise NotImplementedError

    async def update_event_zone_profile(
        self, event_zone_id: int, payload: EventZoneProfileUpdate
    ) -> EventZone:
        """Updates a zone's name and ticket price."""
        raise NotImplementedError

    async def replace_event_zone_seats(
        self, event_zone_id: int, seats: list[EventSeatLayout]
    ) -> EventSeatSyncCountsInternal:
        """Syncs a zone's layout to `seats` in one transaction, keyed on
        `(row, col)`: insert new positions, soft-delete missing ones, then
        renumber every surviving seat 1..N in `(row, col)` order.
        """
        raise NotImplementedError

    # ----------------------------------------------------------------------- #
    # Rule inputs
    # ----------------------------------------------------------------------- #
    async def get_event_zone_pricings(self, event_id: int) -> list[EventZonePricingInternal]:
        """Every zone's price, used by the publish rule."""
        raise NotImplementedError

    async def get_event_order_stats(self, event_id: int) -> EventOrderStatsInternal:
        """Counts the event's pending and confirmed orders, used by the
        unpublish and delete rules.
        """
        raise NotImplementedError

    async def count_occupied_seats_in_zone(self, event_zone_id: int) -> int:
        """Counts LOCKED / RESERVED seats in the zone, so a layout rewrite
        cannot silently drop a seat somebody is holding.
        """
        raise NotImplementedError
