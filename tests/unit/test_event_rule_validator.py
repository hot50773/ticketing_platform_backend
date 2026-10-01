"""Unit tests for EventRuleValidator.

No database, no Redis. Time comes from an injected fixed clock so the
"start_at must be in the future" rule can never go flaky.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.exceptions import (
    ActiveOrdersExistError,
    EventHasNoZoneError,
    EventPublishedError,
    InvalidEventTimeError,
    TicketPriceNotReadyError,
)
from app.schemas.event import (
    EventHoldingTime,
    EventOrderStatsInternal,
    EventStateInternal,
    EventZoneInternal,
)
from app.services.validators import EventRuleValidator

NOW = datetime(2026, 9, 2, 0, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def validator() -> EventRuleValidator:
    return EventRuleValidator(clock=lambda: NOW)


def zone(name: str, price: int | None) -> EventZoneInternal:
    return EventZoneInternal(id=1, name=name, price=price)


def holding_time(start_at, end_at) -> EventHoldingTime:
    return EventHoldingTime(start_at=start_at, end_at=end_at)


def event_state(is_active: bool, id: int = 1) -> EventStateInternal:
    """start_at is required by the DTO but no publish-side rule reads it."""
    return EventStateInternal(
        id=id, is_active=is_active, start_at=NOW + timedelta(days=1)
    )


# --------------------------------------------------------------------------- #
# A. validate_holding_time
# --------------------------------------------------------------------------- #
def test_holding_time_in_the_future_passes(validator):
    validator.validate_holding_time(
        holding_time(NOW + timedelta(days=1), NOW + timedelta(days=1, hours=3))
    )


def test_holding_time_rejects_past_start(validator):
    with pytest.raises(InvalidEventTimeError):
        validator.validate_holding_time(
            holding_time(NOW - timedelta(seconds=1), NOW + timedelta(days=1))
        )


def test_holding_time_rejects_start_exactly_now(validator):
    """The spec says "要大於現在", so equality is a violation."""
    with pytest.raises(InvalidEventTimeError):
        validator.validate_holding_time(holding_time(NOW, NOW + timedelta(hours=3)))


def test_holding_time_rejects_end_equal_to_start(validator):
    """The spec says "要晚於 started_at", so equality is a violation."""
    start = NOW + timedelta(days=1)
    with pytest.raises(InvalidEventTimeError):
        validator.validate_holding_time(holding_time(start, start))


def test_holding_time_rejects_end_before_start(validator):
    start = NOW + timedelta(days=1)
    with pytest.raises(InvalidEventTimeError):
        validator.validate_holding_time(holding_time(start, start - timedelta(seconds=1)))


def test_holding_time_compares_instants_not_wall_clock(validator):
    """A +08:00 datetime is judged on its instant; the schema already made it UTC."""
    start = datetime(2026, 9, 3, 8, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    validator.validate_holding_time(holding_time(start, start + timedelta(hours=3)))


# --------------------------------------------------------------------------- #
# B. validate_unpublished
# --------------------------------------------------------------------------- #
def test_unpublished_event_is_editable(validator):
    validator.validate_unpublished(event_state(is_active=False))


def test_published_event_is_not_editable(validator):
    with pytest.raises(EventPublishedError) as exc:
        validator.validate_unpublished(event_state(id=7, is_active=True))
    assert "7" in str(exc.value)


# --------------------------------------------------------------------------- #
# C. validate_publishable
# --------------------------------------------------------------------------- #
def test_publishable_when_every_zone_is_priced(validator):
    validator.validate_publishable([zone("A", 4500), zone("B", 2500)])


def test_zero_is_a_valid_price(validator):
    """price is nullable, so 0 means a genuinely free zone, not "unset"."""
    validator.validate_publishable([zone("Free", 0)])


def test_not_publishable_when_a_zone_has_no_price(validator):
    with pytest.raises(TicketPriceNotReadyError) as exc:
        validator.validate_publishable([zone("A", 4500), zone("B", None)])
    assert "B" in str(exc.value)
    assert "A" not in str(exc.value)


def test_not_publishable_when_no_zone_is_priced(validator):
    with pytest.raises(TicketPriceNotReadyError):
        validator.validate_publishable([zone("A", None), zone("B", None)])


def test_not_publishable_without_any_zone(validator):
    """An empty zone list would make the price rule vacuously true."""
    with pytest.raises(EventHasNoZoneError):
        validator.validate_publishable([])


# --------------------------------------------------------------------------- #
# D. validate_no_active_order
# --------------------------------------------------------------------------- #
def test_no_live_order_passes(validator):
    validator.validate_no_active_order(EventOrderStatsInternal(active_order_count=0))


def test_live_order_blocks_the_operation(validator):
    with pytest.raises(ActiveOrdersExistError) as exc:
        validator.validate_no_active_order(EventOrderStatsInternal(active_order_count=3))
    assert "3" in str(exc.value)
