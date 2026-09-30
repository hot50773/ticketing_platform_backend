"""Unit tests for OrderValidator's create-order rules.

No database, no Redis. Time comes from an injected fixed clock, so "the event
has not started yet" can never go flaky.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.exceptions import (
    EntityNotFoundError,
    EventNotPublishedError,
    EventStartedError,
    TicketPriceNotReadyError,
)
from app.schemas.event import EventSeatPriceInternal, EventStateInternal
from app.services.validators.order import OrderValidator

NOW = datetime(2026, 9, 30, 0, 0, 0, tzinfo=timezone.utc)
EVENT_ID = 7


@pytest.fixture
def validator() -> OrderValidator:
    return OrderValidator(clock=lambda: NOW)


def event_state(is_active: bool = True, start_at: datetime | None = None):
    return EventStateInternal(
        id=EVENT_ID,
        is_active=is_active,
        start_at=start_at or NOW + timedelta(days=1),
    )


def seat(event_seat_id: int, price: int | None = 1500, event_zone_id: int = 10):
    return EventSeatPriceInternal(
        event_seat_id=event_seat_id, event_zone_id=event_zone_id, price=price
    )


# --------------------------------------------------------------------------- #
# A. validate_event_orderable
# --------------------------------------------------------------------------- #
def test_published_event_before_start_is_orderable(validator):
    validator.validate_event_orderable(event_state())


def test_unpublished_event_is_rejected(validator):
    with pytest.raises(EventNotPublishedError):
        validator.validate_event_orderable(event_state(is_active=False))


def test_unpublished_event_is_rejected_even_before_start(validator):
    """Publication is judged first, so the caller is not told the sale closed
    when it never opened."""
    with pytest.raises(EventNotPublishedError):
        validator.validate_event_orderable(
            event_state(is_active=False, start_at=NOW - timedelta(hours=1))
        )


def test_started_event_is_rejected(validator):
    with pytest.raises(EventStartedError):
        validator.validate_event_orderable(
            event_state(start_at=NOW - timedelta(seconds=1))
        )


def test_event_starting_exactly_now_is_rejected(validator):
    """Sales close at start_at, so equality is already too late."""
    with pytest.raises(EventStartedError):
        validator.validate_event_orderable(event_state(start_at=NOW))


# --------------------------------------------------------------------------- #
# B. validate_seats_orderable
# --------------------------------------------------------------------------- #
def test_every_requested_seat_resolved_and_priced_passes(validator):
    validator.validate_seats_orderable(EVENT_ID, [1, 2], [seat(1), seat(2)])


def test_unresolved_seat_is_not_found(validator):
    """A seat that does not exist, was deleted, or belongs to another event all
    arrive here the same way: absent from the resolved list."""
    with pytest.raises(EntityNotFoundError) as exc:
        validator.validate_seats_orderable(EVENT_ID, [1, 2], [seat(1)])
    assert "2" in str(exc.value)


def test_unpriced_zone_is_rejected(validator):
    with pytest.raises(TicketPriceNotReadyError):
        validator.validate_seats_orderable(EVENT_ID, [1], [seat(1, price=None)])


def test_free_seat_is_not_an_unpriced_seat(validator):
    """0 is a price - a genuinely free zone - and only NULL means unset."""
    validator.validate_seats_orderable(EVENT_ID, [1], [seat(1, price=0)])
