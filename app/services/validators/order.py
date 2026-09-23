"""Pure business-rule validation for the Order lifecycle.

- Holds no I/O: the Service fetches the state and hands it in, so every rule is
unit-testable without a database or Redis.
- Injected into EventService (SRP).
- All datetimes are timezone-aware UTC by the time they arrive here.
"""
from collections.abc import Callable
from datetime import datetime

from app.libs.clock.functional import now_utc
from app.schemas.event import (
    EventZoneWithSeatInternal,
)
from app.schemas.order import OrderCreate


class OrderValidator:
    """Validates Order domain rules"""

    def __init__(self, clock: Callable[[], datetime] = now_utc):
        self._now = clock

    @staticmethod
    def validate_publishable(event_zones: list[EventZoneWithSeatInternal]) -> None:
        pass

    def validate_create_order(self, payload: OrderCreate):
        """
        1. event 尚未結束
        """
        pass