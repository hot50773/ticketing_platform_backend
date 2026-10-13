"""Unit tests for OrderCalculator.

The clock is injected so payment_deadline is an exact expected value rather
than a range.
"""
from datetime import datetime, timedelta, timezone

from app.schemas.event import EventSeatsPricingInternal
from app.schemas.order import OrderCreate
from app.services.calculators.order import OrderCalculator

NOW = datetime(2026, 9, 30, 0, 0, 0, tzinfo=timezone.utc)


def calculator(payment_window: timedelta = timedelta(minutes=10)) -> OrderCalculator:
    return OrderCalculator(payment_window=payment_window, clock=lambda: NOW)


def seat(event_seat_id: int, price: int, event_zone_id: int = 10):
    return EventSeatsPricingInternal(
        event_seat_id=event_seat_id, event_zone_id=event_zone_id, price=price
    )


def payload(event_seat_ids: list[int]):
    return OrderCreate(user_id=1, event_id=7, event_seat_ids=event_seat_ids)


def test_each_ticket_takes_its_own_zone_price():
    pricing = calculator().calculate_order_price(
        payload([1, 2]), [seat(1, 1500, event_zone_id=10), seat(2, 800, event_zone_id=11)]
    )
    assert [(t.event_seat_id, t.amount) for t in pricing.tickets] == [(1, 1500), (2, 800)]


def test_checkout_amount_is_the_ticket_total():
    pricing = calculator().calculate_order_price(payload([1, 2]), [seat(1, 1500), seat(2, 800)])
    assert pricing.checkout_amount == 2300


def test_tickets_follow_the_requested_order_not_the_query_order():
    pricing = calculator().calculate_order_price(payload([2, 1]), [seat(1, 1500), seat(2, 800)])
    assert [ticket.event_seat_id for ticket in pricing.tickets] == [2, 1]


def test_payment_deadline_is_now_plus_the_hold_window():
    pricing = calculator(timedelta(minutes=10)).calculate_order_price(payload([1]), [seat(1, 1500)])
    assert pricing.payment_deadline == NOW + timedelta(minutes=10)
