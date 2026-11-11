"""Pure business-rule validation for the Event lifecycle.

Holds no I/O: the Service fetches the state and hands it in, so every rule is
unit-testable without a database or Redis. Injected into EventService (SRP).
All datetimes are timezone-aware UTC by the time they arrive here.
"""
from collections.abc import Callable
from datetime import datetime

from app.exceptions import (
    EVENT_NOT_FOUND_MSG,
    ActiveOrdersExistError,
    EntityNotFoundError,
    SeatOccupiedError,
    EventHasNoZoneError,
    EventPublishedError,
    InvalidEventTimeError,
    TicketPriceNotReadyError,
)
from app.libs.clock.functional import now_utc
from app.schemas.event import (
    EventDetailResponse,
    EventHoldingTime,
    EventOrderStatsInternal,
    EventStateInternal,
    EventZoneWithSeatInternal,
)

class EventRuleValidator:
    """Validates Event domain rules. Raises on violation, returns None on success."""

    def __init__(self, clock: Callable[[], datetime] = now_utc):
        self._now = clock

    def validate_holding_time(self, holding_time: EventHoldingTime) -> None:
        """start_at must be strictly in the future, end_at strictly after it."""
        start_at, end_at = holding_time.start_at, holding_time.end_at
        now = self._now()
        if start_at <= now:
            raise InvalidEventTimeError(
                f"start_at {start_at.isoformat()} must be later than now {now.isoformat()}"
            )
        if end_at <= start_at:
            raise InvalidEventTimeError(
                f"end_at {end_at.isoformat()} must be later than "
                f"start_at {start_at.isoformat()}"
            )

    def validate_unpublished(self, event_state: EventStateInternal) -> None:
        """Guards every operation that a published event must not allow:
        changing the holding time, and adding or reshaping zones and seats.

        Zone name and price are exempt - they stay editable after publishing.
        """
        if event_state.is_active:
            raise EventPublishedError(
                f"Event {event_state.id} is published; "
                f"this operation is only allowed while it is unpublished"
            )

    @staticmethod
    def validate_publicly_visible(event: EventStateInternal | EventDetailResponse) -> None:
        """A draft reads as missing on public routes: its name, date and prices are not public yet."""
        if not event.is_active:
            raise EntityNotFoundError(EVENT_NOT_FOUND_MSG.format(event_id=event.id))

    @staticmethod
    def validate_publishable(event_zones: list[EventZoneWithSeatInternal]) -> None:
        """An event goes live only with at least one zone, all of them priced."""
        if not event_zones:
            raise EventHasNoZoneError("Cannot publish an event that has no zone")

        unpriced = [zone.name for zone in event_zones if zone.price is None]
        if unpriced:
            raise TicketPriceNotReadyError(
                f"Ticket price is not set for zone(s): {', '.join(unpriced)}"
            )

    @staticmethod
    def validate_no_occupied_seat(occupied_seat_count: int) -> None:
        """Guards a layout rewrite: a seat somebody is holding must not be
        dropped, even while the event is unpublished."""
        if occupied_seat_count > 0:
            raise SeatOccupiedError(
                f"{occupied_seat_count} seat(s) in this zone are locked or reserved"
            )

    @staticmethod
    def validate_no_active_order(order_stats: EventOrderStatsInternal) -> None:
        """Guards unpublishing and deleting: neither is allowed while orders exist."""
        if order_stats.active_order_count > 0:
            raise ActiveOrdersExistError(
                f"This event still has {order_stats.active_order_count} order(s)"
            )
