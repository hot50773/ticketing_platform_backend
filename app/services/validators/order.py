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
from app.schemas.event import EventSeatPriceInternal, EventStateInternal
from app.schemas.order import OrderStateInternal


class OrderValidator:
    """Validates Order domain rules. Raises on violation, returns None on success."""

    def __init__(self, clock: Callable[[], datetime] = now_utc):
        self._now = clock

    def validate_event_orderable(self, state: EventStateInternal) -> None:
        """flow: published -> not started yet.

        Publication is checked first: an unpublished event is not a sale that
        closed, it is a sale that never opened, and telling the caller it has
        already started would be a lie.

        Sales close at start_at rather than end_at. A seat sold after the doors
        open is one the customer cannot use, and the ten-minute hold would
        outlive the moment it was bought for.
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
    def validate_seats_orderable(
        event_id: int,
        event_seat_ids: list[int],
        seats: list[EventSeatPriceInternal],
    ) -> None:
        """Every requested seat belongs to this event and carries a price.

        Shape rules - a non-empty seat list, no repeated seat - live in
        OrderCreate as schema constraints, so a malformed request earns a 422
        at the boundary instead of a 409 from in here.

        `seats` is what the aggregate could resolve, so a seat missing from it
        is one that does not exist, was soft-deleted, or belongs to another
        event. All three are the caller naming something that is not there, so
        all three come back as a 404 rather than a rule violation.

        An unpriced zone should be unreachable - publishing rejects one, and a
        published event cannot gain a zone - but the rule is stated anyway: the
        alternative is the calculator meeting a NULL price and either crashing
        or quietly selling a seat for nothing.

        Whether somebody else is already holding the seat is NOT decided here.
        That lives in Redis and is checked by OrderService before it prices the
        order.
        """
        resolved = {seat.event_seat_id for seat in seats}
        missing = [seat_id for seat_id in event_seat_ids if seat_id not in resolved]
        if missing:
            raise EntityNotFoundError(
                EVENT_SEAT_NOT_FOUND_MSG.format(
                    event_seat_ids=missing, event_id=event_id
                )
            )

        unpriced = sorted({seat.event_zone_id for seat in seats if seat.price is None})
        if unpriced:
            raise TicketPriceNotReadyError(
                f"Ticket price is not set for event zone(s): {unpriced}"
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
