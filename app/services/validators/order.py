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
    EVENT_SEAT_NOT_FOUND_MSG,
    EntityNotFoundError,
    EventNotPublishedError,
    EventStartedError,
    OrderNotCancellableError,
    OrderNotDeletableError,
    OrderNotPayableError,
    PaymentDeadlinePassedError,
    TicketPriceNotReadyError,
)
from app.libs.clock.functional import now_utc
from app.schemas.event import EventSeatsPricingInternal, EventStateInternal
from app.schemas.order import OrderCreate, OrderStateInternal


class OrderValidator:
    """Validates Order domain rules. Raises on violation, returns None on success."""

    def __init__(self, clock: Callable[[], datetime] = now_utc):
        self._now = clock

    def validate_event_orderable(self, state: EventStateInternal) -> None:
        """flow: published -> not started yet.

        Publication first, or an event that never opened gets told the sale
        closed. Sales close at start_at, not end_at - a seat sold after the
        doors open is one nobody can use.
        """
        if not state.is_active:
            raise EventNotPublishedError(
                f"Event {state.id} is not published and cannot be ordered from"
            )

        now = self._now()
        if state.start_at <= now:
            raise EventStartedError(
                f"Event {state.id} started at {state.start_at.isoformat()}; "
                f"sales closed before now {now.isoformat()}"
            )

    @staticmethod
    def validate_seats_input(
        payload: OrderCreate, event_seats: EventSeatsPricingInternal
    ) -> None:
        """Every requested seat belongs to this event and carries a price.

        A seat missing from `seats` does not exist, was soft-deleted, or belongs
        to another event - all three are a 404, not a rule violation.

        The unpriced case should be unreachable (publishing rejects unpriced
        zones), but the calculator must never meet a NULL price.

        Whether somebody else already holds the seat is Redis's call, not this
        one.
        """
        seat_pricing_data = event_seats.seats

        existing_seat_ids = {seat.event_seat_id for seat in seat_pricing_data}
        missing_seat_ids = [
            seat_id for seat_id in payload.event_seat_ids
            if seat_id not in existing_seat_ids
        ]
        if missing_seat_ids:
            raise EntityNotFoundError(
                EVENT_SEAT_NOT_FOUND_MSG.format(event_seat_ids=missing_seat_ids, event_id=payload.event_id)
            )
        if len(existing_seat_ids) != len(payload.event_seat_ids):
            raise ValueError("duplicate event_seat_id in {event_seat_ids}".format(event_seat_ids=payload.event_seat_ids))

        unpriced_zone_ids = sorted({seat.event_zone_id for seat in seat_pricing_data if seat.price is None})
        if unpriced_zone_ids:
            err_msg = "Ticket price is not set for event zone(s) {event_zone_ids} in event {event_id}".format(event_zone_ids=unpriced_zone_ids, event_id=payload.event_id)
            raise TicketPriceNotReadyError(err_msg)

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
