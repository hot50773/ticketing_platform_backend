"""Money and deadlines for a new order.

Split out of OrderService for the same reason OrderValidator is: pricing an
order is its own job, and it is worth testing without a database. Holds no I/O
- the Service resolves the seats through the aggregate and hands the prices in.

Nothing here reads the request for an amount. The client chooses seats; what
they cost and how long they are held is the backend's decision.
"""
from collections.abc import Callable
from datetime import datetime, timedelta

from app.libs.clock.functional import now_utc
from app.schemas.event import EventSeatPriceInternal
from app.schemas.order import OrderPricingInternal, OrderTicketPricingInternal
# The cart hold from the spec: a seat is the customer's for ten minutes, then
# the expiry job releases the order and the seat goes back on sale.
DEFAULT_PAYMENT_WINDOW = timedelta(minutes=10)


class OrderCalculator:
    """Turns the seats a customer picked into what the order rows will say."""

    def __init__(
        self,
        payment_window: timedelta = DEFAULT_PAYMENT_WINDOW,
        clock: Callable[[], datetime] = now_utc,
    ):
        self._payment_window = payment_window
        self._now = clock

    def price_order(
        self, event_seat_ids: list[int], seats: list[EventSeatPriceInternal]
    ) -> OrderPricingInternal:
        """Prices every seat at its zone's price and totals them.

        Tickets come back in the order they were requested, not in whatever
        order the database returned the seats.

        Runs after OrderValidator.validate_seats_orderable, which is what
        guarantees every requested seat is present here and priced. A KeyError
        or a None price from this method therefore means the two were called
        out of order, and it should fail loudly rather than be defended against
        with a fallback price.
        """
        price_by_seat = {seat.event_seat_id: seat.price for seat in seats}
        tickets = [
            OrderTicketPricingInternal(
                event_seat_id=seat_id, amount=price_by_seat[seat_id]
            )
            for seat_id in event_seat_ids
        ]
        return OrderPricingInternal(
            tickets=tickets,
            checkout_amount=sum(ticket.amount for ticket in tickets),
            payment_deadline=self._now() + self._payment_window,
        )
