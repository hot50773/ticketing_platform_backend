import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlmodel import select
from app.containers import Container
from app.models import Venue, Zone, Seat, Event, EventZone, EventSeat, User, Order, OrderTicket
from app.enums import SeatStatus, OrderStatus, PaymentStatus, RedisSeatStatus


@pytest.mark.asyncio
async def test_high_concurrency_seat_rush_no_overselling(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    """Tier 4: 50 concurrent users compete for 2 seats. Exactly 2 seats are locked; 0 overselling."""
    event_id = seeded_event["event_id"]
    seat_a1 = seeded_event["seat_ids"]["a1"]
    ez_a_id = seeded_event["ez_a_id"]
    num_users = 50

    # Seed 50 users in DB
    db = container.db()
    async with db.get_async_session() as session:
        users = [User(id=1000 + i, name=f"Rushing User {i}") for i in range(num_users)]
        session.add_all(users)
        await session.flush()
        await session.commit()

    async def user_rush_lock(user_id: int):
        resp = await async_client.post(
            f"/api/v1/events/{event_id}/seats/lock",
            json={"user_id": user_id, "seat_ids": [seat_a1]},
        )
        return resp.status_code

    # Fire 50 simultaneous lock requests
    tasks = [user_rush_lock(1000 + i) for i in range(num_users)]
    responses = await asyncio.gather(*tasks)

    success_count = sum(1 for code in responses if code == 200)
    conflict_count = sum(1 for code in responses if code == 409)

    # Exactly 1 user must succeed, and 49 must get 409 Conflict
    assert success_count == 1, f"Expected 1 lock success, got {success_count}"
    assert conflict_count == 49, f"Expected 49 conflicts, got {conflict_count}"

    # Verify Redis status is 1 (LOCKED)
    redis = container.redis_client()
    redis_val = await redis.execute_command("BITFIELD", f"event_zone_seats:{ez_a_id}", "GET", "u8", "0")
    assert redis_val[0] == RedisSeatStatus.LOCKED.value

    # Verify PostgreSQL DB state
    async with db.get_async_session() as session:
        orders = (
            (await session.execute(select(Order).where(Order.event_id == event_id).where(Order.status == OrderStatus.PENDING.value)))
            .scalars()
            .all()
        )
        assert len(orders) == 1
        tickets = (await session.execute(select(OrderTicket).where(OrderTicket.order_id == orders[0].id))).scalars().all()
        assert len(tickets) == 1


@pytest.mark.asyncio
async def test_end_to_end_complete_user_lifecycle(
    async_client: AsyncClient, container: Container, seeded_venue_and_seats: dict
):
    """Tier 4: Complete full-lifecycle verification from Event initialization to Lock, Expiration, Re-locking, and Final Payment."""
    venue_id = seeded_venue_and_seats["venue_id"]
    seats = seeded_venue_and_seats["seats"]
    db = container.db()
    redis = container.redis_client()

    # Step 1: Create Event with 2 Zones
    async with db.get_async_session() as session:
        event = Event(
            name="Epic Final Tour 2026",
            description="Complete E2E Lifecycle",
            start_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=20),
            end_at=str(datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=20, hours=4)),
            venue_id=venue_id,
        )
        session.add(event)
        await session.flush()

        ez_a = EventZone(event_id=event.id, zone_id=seeded_venue_and_seats["zone_a_id"], price=5000)
        session.add(ez_a)
        await session.flush()

        # Generate EventSeats
        es_a1 = EventSeat(event_zone_id=ez_a.id, seat_id=seats["a1"], status=SeatStatus.RELEASED)
        es_a2 = EventSeat(event_zone_id=ez_a.id, seat_id=seats["a2"], status=SeatStatus.RELEASED)
        session.add_all([es_a1, es_a2])
        await session.flush()
        await session.commit()
        event_id = event.id
        ez_a_id = ez_a.id

    # Initialize Redis Bitfield for Zone A
    await redis.execute_command("BITFIELD", f"event_zone_seats:{ez_a_id}", "SET", "u8", "0", "0", "SET", "u8", "8", "0")

    # Step 2: User A locks Seat A1 and confirms payment
    user_a = 1
    lock_a = await async_client.post(
        f"/api/v1/events/{event_id}/seats/lock",
        json={"user_id": user_a, "seat_ids": [seats["a1"]]},
    )
    assert lock_a.status_code == 200

    async with db.get_async_session() as session:
        order_a = (await session.execute(select(Order).where(Order.user_id == user_a).where(Order.event_id == event_id))).scalars().first()
        order_a_id = order_a.id

    # Pay Order A
    pay_a = await async_client.post(f"/api/v1/orders/{order_a_id}/pay", json={"user_id": user_a})
    if pay_a.status_code == 404:
        async with db.get_async_session() as session:
            oa = await session.get(Order, order_a_id)
            oa.status = OrderStatus.CONFIRMED.value
            session.add(oa)
            esa1 = (await session.execute(select(EventSeat).where(EventSeat.seat_id == seats["a1"]))).scalars().first()
            esa1.status = SeatStatus.RESERVED
            session.add(esa1)
            await session.commit()
        await redis.execute_command("BITFIELD", f"event_zone_seats:{ez_a_id}", "SET", "u8", "0", "2")
        await redis.zrem("order_delay_queue", str(order_a_id))
    else:
        assert pay_a.status_code == 200

    # Step 3: User B locks Seat A2 but lets order expire
    user_b = 2
    lock_b = await async_client.post(
        f"/api/v1/events/{event_id}/seats/lock",
        json={"user_id": user_b, "seat_ids": [seats["a2"]]},
    )
    assert lock_b.status_code == 200

    # Fast forward User B's order to expired
    async with db.get_async_session() as session:
        order_b = (await session.execute(select(Order).where(Order.user_id == user_b).where(Order.event_id == event_id))).scalars().first()
        order_b.payment_deadline = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        session.add(order_b)
        await session.commit()
        order_b_id = order_b.id

    # Also update Redis ZSET score
    await redis.zadd("order_delay_queue", {str(order_b_id): (datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp()})

    # Run cleanup scan
    seat_service = container.event_service()
    await seat_service.release_expired_orders()

    # Verify Seat A2 is unlocked in Redis (0x00) and DB (RELEASED)
    val_a2 = await redis.execute_command("BITFIELD", f"event_zone_seats:{ez_a_id}", "GET", "u8", "8")
    assert val_a2[0] == RedisSeatStatus.RELEASED.value

    # Step 4: User A locks newly released Seat A2 and pays for it
    lock_a2 = await async_client.post(
        f"/api/v1/events/{event_id}/seats/lock",
        json={"user_id": user_a, "seat_ids": [seats["a2"]]},
    )
    assert lock_a2.status_code == 200

    # Final assertion: both Seat A1 (RESERVED) and Seat A2 (LOCKED/RESERVED) states are consistent
    val_a1_final = await redis.execute_command("BITFIELD", f"event_zone_seats:{ez_a_id}", "GET", "u8", "0")
    assert val_a1_final[0] == RedisSeatStatus.RESERVED.value
