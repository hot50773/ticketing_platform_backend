"""Cancelling and expiring an order against both stores.

The order change and the Postgres release share one transaction holding only
`FOR SHARE` on the event, so they commit or roll back together and never queue
behind a booking. The question each test asks: can somebody buy the seat now?
"""
import asyncio
from datetime import timedelta

import pytest
from sqlmodel import select

from app.containers import Container
from app.enums import OrderStatus, PaymentStatus, SeatStatus
from app.exceptions import SeatNotReleasableError
from app.libs.clock.functional import now_utc
from app.models import Event, EventSeat, EventZone, Order, OrderTicket
from app.schemas.event import EventSeatsPricingInternal, SeatPriceInternal


@pytest.fixture
async def booked(container: Container, seeded_event: dict):
    """An event whose bitmaps are warm, with a factory for orders that really
    hold their seats in both stores."""
    cache = container.seat_layout_cache()
    event_id = seeded_event["event_id"]
    async with container.event_repository().load(event_id) as event:
        layout = await event.seat_layout()
    await cache.init_seat_layouts(layout)

    async def _book(*, overdue_by: timedelta | None = None) -> tuple[int, int]:
        now = now_utc()
        async with container.db().get_async_session() as session:
            seat = (await session.execute(
                select(EventSeat)
                .join(EventZone, EventZone.id == EventSeat.event_zone_id)
                .where(EventZone.event_id == event_id)
                .where(EventSeat.status == SeatStatus.AVAILABLE)
                .order_by(EventSeat.id)
                .limit(1)
            )).scalar_one()

            order = Order(
                customer_id=seeded_event["customer_id"],
                event_id=event_id,
                checkout_amount=1500,
                status=OrderStatus.PENDING,
                payment_status=PaymentStatus.PENDING,
                payment_deadline=(
                    now - overdue_by if overdue_by is not None else now + timedelta(minutes=10)
                ),
            )
            session.add(order)
            await session.flush()
            session.add(OrderTicket(order_id=order.id, event_seat_id=seat.id, amount=1500))
            seat.status = SeatStatus.RESERVED
            session.add(seat)
            pricing = SeatPriceInternal(
                event_seat_id=seat.id,
                event_zone_id=seat.event_zone_id,
                seat_serial_number=seat.serial_number,
                price=1500,
            )
            order_id, seat_id = order.id, seat.id
            await session.commit()

        await cache.check_and_lock_seats_atomic(
            EventSeatsPricingInternal(event_id=event_id, seats=[pricing])
        )
        return order_id, seat_id

    return {
        "book": _book,
        "cache": cache,
        "container": container,
        "event_id": event_id,
        "service": container.order_service(),
        "customer_id": seeded_event["customer_id"],
    }


async def _buyable(booked, seat_id: int) -> bool:
    async with booked["container"].db().get_async_session() as session:
        seat = await session.get(EventSeat, seat_id)
    try:
        await booked["cache"].check_and_lock_seats_atomic(
            EventSeatsPricingInternal(
                event_id=booked["event_id"],
                seats=[SeatPriceInternal(
                    event_seat_id=seat.id, event_zone_id=seat.event_zone_id,
                    seat_serial_number=seat.serial_number, price=1,
                )],
            )
        )
    except Exception:
        return False
    return seat.status == SeatStatus.AVAILABLE


async def _row(container, model, row_id: int):
    async with container.db().get_async_session() as session:
        return await session.get(model, row_id)


@pytest.mark.asyncio
async def test_a_cancelled_order_puts_its_seat_back_on_sale(booked):
    order_id, seat_id = await booked["book"]()

    await booked["service"].cancel_order(order_id, booked["customer_id"])

    assert (await _row(booked["container"], Order, order_id)).status == OrderStatus.CANCELLED
    assert await _buyable(booked, seat_id)


@pytest.mark.asyncio
async def test_an_expired_order_puts_its_seat_back_on_sale(booked):
    order_id, seat_id = await booked["book"](overdue_by=timedelta(seconds=1))

    await booked["service"].check_order_expiration(order_id)

    assert (await _row(booked["container"], Order, order_id)).deleted_at is not None
    assert await _buyable(booked, seat_id)


@pytest.mark.asyncio
async def test_a_failed_release_leaves_the_order_as_it_was(booked):
    """One transaction: if the seat cannot go back, the cancel does not stick."""
    order_id, seat_id = await booked["book"]()
    async with booked["container"].db().get_async_session() as session:
        seat = await session.get(EventSeat, seat_id)
        seat.deleted_at = now_utc()
        await session.commit()

    with pytest.raises(SeatNotReleasableError):
        await booked["service"].cancel_order(order_id, booked["customer_id"])

    assert (await _row(booked["container"], Order, order_id)).status == OrderStatus.PENDING


@pytest.mark.asyncio
async def test_releasing_does_not_queue_behind_a_booking(booked):
    """A booking holds FOR SHARE on the event; a release must not wait for it."""
    order_id, _ = await booked["book"]()

    booking = booked["container"].db().get_async_session()
    await booking.__aenter__()
    try:
        await booking.execute(
            select(Event)
            .where(Event.id == booked["event_id"])
            .with_for_update(read=True)
        )
        # A FOR UPDATE here would queue behind the booking and time out.
        await asyncio.wait_for(booked["service"].cancel_order(order_id, booked["customer_id"]), timeout=3.0)
    finally:
        await booking.rollback()
        await booking.__aexit__(None, None, None)
