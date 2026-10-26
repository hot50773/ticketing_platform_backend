"""Money and deadlines for a new order. No I/O - the Service resolves the seats
and hands the prices in, so this is testable without a database.

Nothing here reads an amount from the request: the client picks seats, the
backend says what they cost.
"""
from collections.abc import Callable
from datetime import datetime, timedelta

from app.libs.clock.functional import now_utc
from app.schemas.event import EventSeatsPricingInternal, EventLayoutInternal
from app.schemas.order import (
    OrderCreate,
    OrderCreateInternal,
    OrderTicketPricingInternal,
)

# The cart hold from the spec; the expiry job releases the order once it passes.
DEFAULT_PAYMENT_WINDOW = timedelta(seconds=10)
# DEFAULT_PAYMENT_WINDOW = timedelta(minutes=10)


class OrderCalculator:
    """Turns the seats a customer picked into what the order rows will say."""

    def __init__(
        self,
        payment_window: timedelta = DEFAULT_PAYMENT_WINDOW,
        clock: Callable[[], datetime] = now_utc,
    ):
        self._payment_window = payment_window
        self._now = clock

    def calculate_order_price(
        self, payload: OrderCreate, event_seats_pricing: EventSeatsPricingInternal
    ) -> OrderCreateInternal:
        """Prices every seat at its zone's price, in the order requested.

        Runs after validate_seats_orderable, which is what guarantees every
        seat is here and priced. A KeyError or a None amount means the two ran
        out of order - let it fail rather than invent a fallback price.
        """
        event_seat_id_to_price_map = {seat.event_seat_id: seat.price for seat in event_seats_pricing.seats}

        tickets = []
        for event_seat_id in payload.event_seat_ids:
            price = event_seat_id_to_price_map.get(event_seat_id)
            if price is None:
                raise ValueError("seat price is not set for seat {event_seat_id}".format(event_seat_id=event_seat_id))
            ticket = OrderTicketPricingInternal(
                event_seat_id=event_seat_id, amount=price
            )
            tickets.append(ticket)

        return OrderCreateInternal(
            tickets=tickets,
            checkout_amount=sum(ticket.amount for ticket in tickets),
            payment_deadline=self._now() + self._payment_window,
            user_id=payload.user_id,
            comment=payload.comment,
        )
