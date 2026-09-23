"""Pure business-rule validation for the Order lifecycle.

- Holds no I/O: the Service fetches the state and hands it in, so every rule is
  unit-testable without a database or Redis.
- Injected into OrderService (SRP).
- All datetimes are timezone-aware UTC by the time they arrive here.
"""
from collections.abc import Callable
from datetime import datetime

from app.enums import OrderStatus, PaymentStatus
from app.exceptions import (
    OrderNotCancellableError,
    OrderNotDeletableError,
    OrderNotPayableError,
    PaymentDeadlinePassedError,
)
from app.libs.clock.functional import now_utc
from app.schemas.order import OrderCreate, OrderStateInternal


class OrderValidator:
    """Validates Order domain rules. Raises on violation, returns None on success."""

    def __init__(self, clock: Callable[[], datetime] = now_utc):
        self._now = clock

    def validate_create_order(self, payload: OrderCreate) -> None:
        """Rules decidable from the payload alone.

        Shape rules - a non-empty ticket list, no repeated seat - live in
        OrderCreate as schema constraints, so a malformed request earns a 422
        at the boundary instead of a 409 from in here.

        TODO: the "event has not ended and is still on sale" rule needs event
        state, which this class must not fetch. It belongs in a sibling method
        taking an *Internal DTO, with OrderService reading the event through
        EventRepository and passing it in - the same shape as
        validate_publishable on the event side.
        """
        expected = sum(ticket.amount for ticket in payload.tickets)
        if payload.checkout_amount != expected:
            raise OrderNotPayableError(
                f"checkout_amount {payload.checkout_amount} does not match the "
                f"ticket total {expected}"
            )

        if payload.payment_deadline is not None:
            now = self._now()
            if payload.payment_deadline <= now:
                raise PaymentDeadlinePassedError(
                    f"payment_deadline {payload.payment_deadline.isoformat()} "
                    f"must be later than now {now.isoformat()}"
                )

    def validate_payable(self, state: OrderStateInternal) -> None:
        """flow: not cancelled -> not already settled -> no charge in flight
                 -> deadline not passed.

        IN_PROGRESS is rejected rather than retried: the previous attempt may
        still be in the gateway, and charging twice is the one failure the
        customer will not forgive. A stuck claim is cleared by the expiry job,
        not by the next request.
        """
        if state.status == OrderStatus.CANCELLED:
            raise OrderNotPayableError(f"Order {state.id} is cancelled")

        if state.payment_status == PaymentStatus.SUCCESS:
            raise OrderNotPayableError(f"Order {state.id} is already paid")

        if state.payment_status == PaymentStatus.IN_PROGRESS:
            raise OrderNotPayableError(
                f"Order {state.id} already has a payment in progress"
            )

        if state.payment_deadline is not None and state.payment_deadline <= self._now():
            raise PaymentDeadlinePassedError(
                f"Order {state.id} passed its payment deadline "
                f"{state.payment_deadline.isoformat()}"
            )

    def validate_cancellable(self, state: OrderStateInternal) -> None:
        """A paid order is refunded, not cancelled - cancelling it would leave
        the money collected and the seats released."""
        if state.status == OrderStatus.CANCELLED:
            raise OrderNotCancellableError(f"Order {state.id} is already cancelled")

        if state.payment_status == PaymentStatus.SUCCESS:
            raise OrderNotCancellableError(
                f"Order {state.id} is paid for; refund it instead of cancelling"
            )

    def validate_deletable(self, state: OrderStateInternal) -> None:
        """Soft-deleting releases the seats, so a settled purchase must not go
        this way. Cancelled and unpaid orders are both fine to release."""
        if state.payment_status == PaymentStatus.SUCCESS:
            raise OrderNotDeletableError(
                f"Order {state.id} is paid for; refund it before releasing its seats"
            )
