"""What the release worker picks up, against a real database.

Two questions, one per query: whose hold has run out, and whose seats are
still held by an order that is already gone. Both run on a schedule with
nobody watching, so what they must *not* pick up matters as much as what they
must.
"""
from datetime import timedelta

import pytest
from sqlmodel import select

from app.containers import Container
from app.enums import OrderStatus, PaymentStatus, SeatStatus
from app.libs.clock.functional import now_utc
from app.models import EventSeat, EventZone, Order, OrderTicket


@pytest.fixture
async def make_order(container: Container, seeded_event: dict):
    """Builds an order holding the event's seats, in whatever state a test needs."""
    event_id = seeded_event["event_id"]

    async def _make(
        *,
        seats: int = 1,
        status: OrderStatus = OrderStatus.PENDING,
        payment_status: PaymentStatus = PaymentStatus.PENDING,
        overdue_by: timedelta | None = None,
        deleted: bool = False,
        seat_status: SeatStatus = SeatStatus.RESERVED,
    ) -> int:
        now = now_utc()
        async with container.db().get_async_session() as session:
            free = (
                await session.execute(
                    select(EventSeat.id)
                    .join(EventZone, EventZone.id == EventSeat.event_zone_id)
                    .where(EventZone.event_id == event_id)
                    .where(EventSeat.status == SeatStatus.AVAILABLE)
                    .order_by(EventSeat.id)
                    .limit(seats)
                )
            ).scalars().all()

            order = Order(
                user_id=seeded_event["user_id"],
                event_id=event_id,
                checkout_amount=1500 * len(free),
                status=status,
                payment_status=payment_status,
                payment_deadline=(
                    now - overdue_by if overdue_by is not None else now + timedelta(minutes=10)
                ),
                deleted_at=now if deleted else None,
            )
            session.add(order)
            await session.flush()

            for seat_id in free:
                session.add(OrderTicket(
                    order_id=order.id,
                    event_seat_id=seat_id,
                    amount=1500,
                    deleted_at=now if deleted else None,
                ))
                seat = await session.get(EventSeat, seat_id)
                seat.status = seat_status
                session.add(seat)

            await session.commit()
            return order.id

    return _make


# --------------------------------------------------------------------------- #
# Whose hold has run out
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_overdue_pending_order_is_picked_up(container, make_order):
    order_id = await make_order(overdue_by=timedelta(seconds=1))

    found = await container.order_repository().get_expired_order_ids(now_utc(), limit=10)

    assert found == [order_id]


@pytest.mark.asyncio
async def test_a_hold_that_has_not_run_out_is_left_alone(container, make_order):
    await make_order()

    assert await container.order_repository().get_expired_order_ids(now_utc(), limit=10) == []


@pytest.mark.asyncio
async def test_a_deadline_exactly_now_counts_as_run_out(container, make_order):
    """The hold is ten minutes, not ten minutes and a bit. Swept at exactly the
    deadline, so this pins `<=` rather than the clock being a hair late."""
    order_id = await make_order(overdue_by=timedelta(0))
    async with container.db().get_async_session() as session:
        deadline = (await session.get(Order, order_id)).payment_deadline

    found = await container.order_repository().get_expired_order_ids(deadline, limit=10)

    assert found == [order_id]


@pytest.mark.asyncio
async def test_a_payment_in_flight_is_never_taken_away(container, make_order):
    """The provider may already have the customer's money. Releasing these
    seats would sell a ticket somebody is in the middle of paying for."""
    await make_order(
        overdue_by=timedelta(minutes=5), payment_status=PaymentStatus.IN_PROGRESS
    )

    assert await container.order_repository().get_expired_order_ids(now_utc(), limit=10) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        {"payment_status": PaymentStatus.SUCCESS},
        {"status": OrderStatus.CONFIRMED},
        {"status": OrderStatus.CANCELLED},
        {"deleted": True},
    ],
)
async def test_orders_that_are_already_settled_are_left_alone(container, make_order, state):
    await make_order(overdue_by=timedelta(minutes=5), **state)

    assert await container.order_repository().get_expired_order_ids(now_utc(), limit=10) == []


@pytest.mark.asyncio
async def test_the_oldest_hold_goes_first(container, make_order):
    """A capped sweep must not starve the orders that have waited longest."""
    newer = await make_order(overdue_by=timedelta(minutes=1))
    older = await make_order(overdue_by=timedelta(minutes=9))

    found = await container.order_repository().get_expired_order_ids(now_utc(), limit=1)

    assert found == [older] and newer not in found


# --------------------------------------------------------------------------- #
# Whose seats are still held
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_deleted_order_still_holding_seats_is_picked_up(container, make_order):
    order_id = await make_order(seats=2, deleted=True, seat_status=SeatStatus.RESERVED)

    found = await container.order_repository().get_unreleased_seats(limit=10)

    assert {seat.order_id for seat in found} == {order_id}
    assert len(found) == 2
    assert all(seat.event_id and seat.event_zone_id is not None for seat in found)
    assert {seat.serial_number for seat in found} == {0, 1}


@pytest.mark.asyncio
async def test_a_live_order_is_not_a_candidate(container, make_order):
    """Its seats are held because somebody is buying them."""
    await make_order(seat_status=SeatStatus.RESERVED)

    assert await container.order_repository().get_unreleased_seats(limit=10) == []


@pytest.mark.asyncio
async def test_a_deleted_order_whose_seats_came_back_is_done(container, make_order):
    await make_order(deleted=True, seat_status=SeatStatus.AVAILABLE)

    assert await container.order_repository().get_unreleased_seats(limit=10) == []


@pytest.mark.asyncio
async def test_the_cap_counts_orders_not_rows(container, make_order):
    """A two-seat order must come back whole - releasing half of it would leave
    the rest stuck with nothing left pointing at them."""
    await make_order(seats=2, deleted=True)
    await make_order(seats=2, deleted=True)

    found = await container.order_repository().get_unreleased_seats(limit=1)

    assert len({seat.order_id for seat in found}) == 1
    assert len(found) == 2
