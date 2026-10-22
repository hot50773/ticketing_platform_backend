"""The release sweep against both stores at once.

Everything here turns on Postgres and Redis agreeing afterwards, and there is
no transaction spanning the two - so the only honest test is the real pair.
The question each test asks is the same: after this, can somebody buy the seat?
"""
from datetime import timedelta

import pytest
from sqlmodel import select

from app.containers import Container
from app.enums import OrderStatus, PaymentStatus, SeatStatus
from app.libs.clock.functional import now_utc
from app.models import EventSeat, EventZone, Order, OrderTicket
from app.schemas.event import EventSeatsPricingInternal, SeatPriceInternal


@pytest.fixture
async def booked(container: Container, seeded_event: dict):
    """An event whose bitmaps are warm, with a factory for orders that really
    hold their seats in both stores."""
    cache = container.event_layout_cache()
    event_id = seeded_event["event_id"]
    async with container.event_repository().load(event_id) as event:
        layout = await event.event_layout()
    await cache.init_event_layouts(layout)

    async def _book(
        *,
        seats: int = 1,
        overdue_by: timedelta | None = None,
        deleted: bool = False,
        payment_status: PaymentStatus = PaymentStatus.PENDING,
    ) -> int:
        now = now_utc()
        async with container.db().get_async_session() as session:
            free = (await session.execute(
                select(EventSeat)
                .join(EventZone, EventZone.id == EventSeat.event_zone_id)
                .where(EventZone.event_id == event_id)
                .where(EventSeat.status == SeatStatus.AVAILABLE)
                .order_by(EventSeat.id)
                .limit(seats)
            )).scalars().all()

            order = Order(
                user_id=seeded_event["user_id"],
                event_id=event_id,
                checkout_amount=1500 * len(free),
                status=OrderStatus.PENDING,
                payment_status=payment_status,
                payment_deadline=(
                    now - overdue_by if overdue_by is not None else now + timedelta(minutes=10)
                ),
                deleted_at=now if deleted else None,
            )
            session.add(order)
            await session.flush()

            pricing = []
            for seat in free:
                session.add(OrderTicket(
                    order_id=order.id, event_seat_id=seat.id, amount=1500,
                    deleted_at=now if deleted else None,
                ))
                seat.status = SeatStatus.RESERVED
                session.add(seat)
                pricing.append(SeatPriceInternal(
                    event_seat_id=seat.id,
                    event_zone_id=seat.event_zone_id,
                    seat_serial_number=seat.serial_number,
                    price=1500,
                ))
            order_id = order.id
            await session.commit()

        await cache.check_and_lock_seats_atomic(
            EventSeatsPricingInternal(event_id=event_id, seats=pricing)
        )
        return order_id

    return {
        "book": _book,
        "cache": cache,
        "event_id": event_id,
        "sweep": container.seat_release_service(),
        "container": container,
    }


async def _buyable(booked, serials: list[int]) -> bool:
    """The only question that matters: can the next customer take these seats?"""
    async with booked["container"].db().get_async_session() as session:
        zone_id = (await session.execute(
            select(EventZone.id).where(EventZone.event_id == booked["event_id"])
            .order_by(EventZone.id).limit(1)
        )).scalar_one()
    try:
        await booked["cache"].check_and_lock_seats_atomic(
            EventSeatsPricingInternal(
                event_id=booked["event_id"],
                seats=[
                    SeatPriceInternal(
                        event_seat_id=0, event_zone_id=zone_id,
                        seat_serial_number=serial, price=1,
                    )
                    for serial in serials
                ],
            )
        )
        return True
    except Exception:
        return False


async def _order(container, order_id: int) -> Order:
    async with container.db().get_async_session() as session:
        return await session.get(Order, order_id)


@pytest.mark.asyncio
async def test_an_expired_hold_puts_the_seat_back_on_sale(booked):
    await booked["book"](overdue_by=timedelta(seconds=1))

    await booked["sweep"].run()

    assert await _buyable(booked, [0])


@pytest.mark.asyncio
async def test_a_hold_that_is_still_good_is_left_alone(booked):
    order_id = await booked["book"]()

    await booked["sweep"].run()

    assert (await _order(booked["container"], order_id)).deleted_at is None
    assert not await _buyable(booked, [0])


@pytest.mark.asyncio
async def test_a_deleted_order_gives_its_seats_up_without_being_expired(booked):
    """Deleting an order does not release anything itself - the sweep is the
    only thing that hands seats back, whoever asked for the deletion."""
    await booked["book"](deleted=True)

    await booked["sweep"].run()

    assert await _buyable(booked, [0])


@pytest.mark.asyncio
async def test_a_payment_in_flight_keeps_its_seats(booked):
    """The provider may already have the money."""
    order_id = await booked["book"](
        overdue_by=timedelta(minutes=5), payment_status=PaymentStatus.IN_PROGRESS
    )

    await booked["sweep"].run()

    assert (await _order(booked["container"], order_id)).deleted_at is None
    assert not await _buyable(booked, [0])


@pytest.mark.asyncio
async def test_sweeping_twice_changes_nothing_the_second_time(booked):
    await booked["book"](seats=2, overdue_by=timedelta(seconds=1))

    await booked["sweep"].run()
    await booked["sweep"].run()

    assert await _buyable(booked, [0, 1])
    assert await booked["cache"].pending_releases() == []


@pytest.mark.asyncio
async def test_a_release_that_stopped_halfway_is_finished_next_time(booked):
    """Flag written, database already let the seats go, then the process died
    before the bits were cleared. Nothing in Postgres points at this any more."""
    order_id = await booked["book"](deleted=True)
    await booked["cache"].mark_release_pending(booked["event_id"], order_id)
    async with booked["container"].event_repository().load_for_seat_release(
        booked["event_id"]
    ) as event:
        async with booked["container"].db().get_async_session() as session:
            tickets = (await session.execute(
                select(OrderTicket).where(OrderTicket.order_id == order_id)
            )).scalars().all()
        await event.check_and_release_seats([t.event_seat_id for t in tickets])

    assert not await _buyable(booked, [0]), "bits are still set, as after a crash"

    await booked["sweep"].run()

    assert await _buyable(booked, [0])
    assert await booked["cache"].pending_releases() == []


@pytest.mark.asyncio
async def test_a_flag_for_an_order_that_never_existed_is_dropped(booked):
    """Its transaction rolled back, so there is nothing to release - but the
    flag would be carried for ever if nobody cleared it."""
    await booked["cache"].mark_release_pending(booked["event_id"], 9999)

    await booked["sweep"].run()

    assert await booked["cache"].pending_releases() == []
