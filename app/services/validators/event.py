"""Pure business-rule validation for the Event lifecycle.

Holds no I/O: the Service fetches the state and hands it in, so every rule is
unit-testable without a database or Redis. Injected into EventService (SRP).
All datetimes are timezone-aware UTC by the time they arrive here.
"""
from collections.abc import Callable
from datetime import datetime

from app.exceptions import (
    ActiveOrdersExistError,
    EventPublishedError,
    InvalidEventTimeError,
    TicketPriceNotReadyError,
)
from app.libs.clock.functional import now_utc
from app.schemas.event import EventOrderStatsInternal, EventZonePricingInternal


class EventRuleValidator:
    """Validates Event domain rules. Raises on violation, returns None on success."""

    def __init__(self, clock: Callable[[], datetime] = now_utc):
        self._now = clock

    def validate_holding_time(self, start_at: datetime, end_at: datetime) -> None:
        """start_at must be in the future and end_at must be later than start_at.

        Raises InvalidEventTimeError.
        """
        raise NotImplementedError

    def validate_holding_time_editable(self, is_active: bool) -> None:
        """Holding time may only change while the event is unpublished.

        Raises EventPublishedError.
        """
        raise NotImplementedError

    def validate_layout_editable(self, is_active: bool) -> None:
        """Zones and seats may only be added or reshaped while unpublished.

        Zone name and price are exempt: those stay editable after publishing.
        Raises EventPublishedError.
        """
        raise NotImplementedError

    def validate_publishable(self, zones: list[EventZonePricingInternal]) -> None:
        """Every zone must have a price set (not None) before going live.

        Raises TicketPriceNotReadyError.
        """
        raise NotImplementedError

    def validate_unpublishable(self, order_stats: EventOrderStatsInternal) -> None:
        """An event with live orders cannot be taken offline.

        Raises ActiveOrdersExistError.
        """
        raise NotImplementedError

    def validate_deletable(self, order_stats: EventOrderStatsInternal) -> None:
        """An event with confirmed orders cannot be deleted.

        Raises ActiveOrdersExistError.
        """
        raise NotImplementedError
