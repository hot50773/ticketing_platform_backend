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
    OrderNotCancellableError,
    TicketPriceNotReadyError,
)
from app.enums import OrderStatus, PaymentStatus
from app.schemas.event import EventStateInternal
from app.schemas.order import OrderCreate, OrderStateInternal
from app.services.validators.order import OrderValidator
from tests.unit._fakes import a_seat_price as seat, a_seats_pricing

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


def a_pricing(*seats):
    return a_seats_pricing(list(seats), event_id=EVENT_ID)


def payload(event_seat_ids: list[int], event_id: int = EVENT_ID):
    return OrderCreate(user_id=1, event_id=event_id, event_seat_ids=event_seat_ids)


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
    validator.validate_seats_input(payload([1, 2]), a_pricing(seat(1), seat(2)))


def test_unresolved_seat_is_not_found(validator):
    """A seat that does not exist, was deleted, or belongs to another event all
    arrive here the same way: absent from the resolved list."""
    with pytest.raises(EntityNotFoundError) as exc:
        validator.validate_seats_input(payload([1, 2]), a_pricing(seat(1)))
    assert "2" in str(exc.value)


def test_unpriced_zone_is_rejected(validator):
    with pytest.raises(TicketPriceNotReadyError):
        validator.validate_seats_input(payload([1]), a_pricing(seat(1, price=None)))


def test_free_seat_is_not_an_unpriced_seat(validator):
    """0 is a price - a genuinely free zone - and only NULL means unset."""
    validator.validate_seats_input(payload([1]), a_pricing(seat(1, price=0)))


# --------------------------------------------------------------------------- #
# C. validate_cancellable
# --------------------------------------------------------------------------- #
def order_state(
    status: OrderStatus = OrderStatus.PENDING,
    payment_status: PaymentStatus = PaymentStatus.PENDING,
) -> OrderStateInternal:
    return OrderStateInternal(
        id=1,
        status=status,
        payment_status=payment_status,
        payment_deadline=NOW + timedelta(minutes=10),
    )


def test_unpaid_order_is_cancellable(validator):
    validator.validate_cancellable(order_state())


def test_order_with_a_failed_payment_is_cancellable(validator):
    """A failed charge leaves the order retryable, so it can still be dropped."""
    validator.validate_cancellable(order_state(payment_status=PaymentStatus.FAILED))


def test_cancelled_order_is_not_cancelled_twice(validator):
    with pytest.raises(OrderNotCancellableError):
        validator.validate_cancellable(order_state(status=OrderStatus.CANCELLED))


def test_paid_order_is_not_cancellable(validator):
    with pytest.raises(OrderNotCancellableError):
        validator.validate_cancellable(
            order_state(status=OrderStatus.CONFIRMED, payment_status=PaymentStatus.SUCCESS)
        )


def test_order_with_a_charge_in_flight_is_not_cancellable(validator):
    with pytest.raises(OrderNotCancellableError):
        validator.validate_cancellable(order_state(payment_status=PaymentStatus.IN_PROGRESS))
