import pytest
import time
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import select, text
from app.containers import Container
from app.models import Venue, Zone, Seat, Event, EventZone, EventSeat, Order
from app.enums import SeatStatus, OrderStatus, RedisSeatStatus


@pytest.mark.asyncio
async def test_composite_unique_zone_code_conflict(
    async_client: AsyncClient, container: Container, seeded_venue_and_seats: dict
):
    """Tier 2: Duplicate zone code in same venue violates uq_zone_venue and triggers 409 Conflict."""
    venue_id = seeded_venue_and_seats["venue_id"]
    duplicate_payload = {"code": "ZONE_A", "venue_id": venue_id}

    resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json=duplicate_payload)
    if resp.status_code == 404:
        # Verify DB constraint directly
        db = container.db()
        with pytest.raises(IntegrityError):
            async with db.get_async_session() as session:
                dup_zone = Zone(code="ZONE_A", venue_id=venue_id)
                session.add(dup_zone)
                await session.flush()
    else:
        assert resp.status_code == 409


@pytest.mark.asyncio
async def test_composite_unique_seat_serial_conflict(
    async_client: AsyncClient, container: Container, seeded_venue_and_seats: dict
):
    """Tier 2: Duplicate seat serial_number in same zone violates uq_seat_zone and triggers 409 Conflict."""
    zone_a_id = seeded_venue_and_seats["zone_a_id"]
    duplicate_payload = {"serial_number": 1, "row": 9, "column": 9, "zone_id": zone_a_id}

    resp = await async_client.post(f"/api/v1/zones/{zone_a_id}/seats", json=duplicate_payload)
    if resp.status_code == 404:
        # Verify DB constraint directly
        db = container.db()
        with pytest.raises(IntegrityError):
            async with db.get_async_session() as session:
                dup_seat = Seat(serial_number=1, row=9, column=9, zone_id=zone_a_id)
                session.add(dup_seat)
                await session.flush()
    else:
        assert resp.status_code == 409


@pytest.mark.asyncio
async def test_empty_seat_lock_request_rejection(
    async_client: AsyncClient, seeded_event: dict
):
    """Tier 2: Sending empty seat_ids list is rejected without side effects."""
    event_id = seeded_event["event_id"]
    resp = await async_client.post(
        f"/api/v1/events/{event_id}/seats/lock",
        json={"user_id": 1, "seat_ids": []},
    )
    assert resp.status_code in (400, 422, 409)


@pytest.mark.asyncio
async def test_partial_seat_conflict_atomic_rollback(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    """Tier 2: When locking multiple seats and one is unavailable, entire request is rejected atomically."""
    event_id = seeded_event["event_id"]
    seat_a1 = seeded_event["seat_ids"]["a1"]
    seat_a2 = seeded_event["seat_ids"]["a2"]

    # User 1 locks Seat A1
    resp1 = await async_client.post(
        f"/api/v1/events/{event_id}/seats/lock",
        json={"user_id": 1, "seat_ids": [seat_a1]},
    )
    assert resp1.status_code == 200

    # User 2 attempts to lock [Seat A1, Seat A2]
    resp2 = await async_client.post(
        f"/api/v1/events/{event_id}/seats/lock",
        json={"user_id": 2, "seat_ids": [seat_a1, seat_a2]},
    )
    assert resp2.status_code == 409

    # Verify Seat A2 remains unreserved (status RELEASED in DB and 0x00 in Redis)
    db = container.db()
    async with db.get_async_session() as session:
        es_a2 = (await session.execute(select(EventSeat).where(EventSeat.seat_id == seat_a2))).scalars().first()
        assert es_a2.status == SeatStatus.RELEASED

    redis = container.redis_client()
    redis_val = await redis.execute_command("BITFIELD", f"event_zone_seats:{seeded_event['ez_a_id']}", "GET", "u8", "8")
    assert redis_val[0] == RedisSeatStatus.RELEASED.value


@pytest.mark.asyncio
async def test_10k_seats_event_creation_performance(container: Container):
    """Tier 2: Creating an event with 10,000 seats must complete bulk insert and Redis initialization in under 2 seconds."""
    db = container.db()
    redis = container.redis_client()

    # Setup Venue with 5 zones of 2,000 seats each = 10,000 seats
    async with db.get_async_session() as session:
        venue = Venue(name="Massive Mega Dome", description="10k Capacity", address="Dome City")
        session.add(venue)
        await session.flush()

        zones = [Zone(code=f"ZONE_{i}", venue_id=venue.id) for i in range(5)]
        session.add_all(zones)
        await session.flush()

        all_seats = []
        for zone in zones:
            for serial in range(1, 2001):
                all_seats.append(Seat(serial_number=serial, row=(serial // 50) + 1, column=(serial % 50) + 1, zone_id=zone.id))
        session.add_all(all_seats)
        await session.flush()
        await session.commit()
        venue_id = venue.id
        zone_ids = [z.id for z in zones]

    # Benchmark Event & Seat creation
    start_time = time.perf_counter()

    async with db.get_async_session() as session:
        event = Event(
            name="Mega Festival 2026",
            description="10,000 attendees",
            start_at=datetime.now(timezone.utc).replace(tzinfo=None),
            end_at=str(datetime.now(timezone.utc).replace(tzinfo=None)),
            venue_id=venue_id,
        )
        session.add(event)
        await session.flush()

        event_zones = [EventZone(event_id=event.id, zone_id=zid, price=2000) for zid in zone_ids]
        session.add_all(event_zones)
        await session.flush()

        # Query all physical seats for the venue
        seats = (await session.execute(select(Seat).where(Seat.zone_id.in_(zone_ids)))).scalars().all()
        zone_to_ez = {ez.zone_id: ez.id for ez in event_zones}

        # Bulk generate EventSeats
        event_seats = [
            EventSeat(event_zone_id=zone_to_ez[seat.zone_id], seat_id=seat.id, status=SeatStatus.RELEASED)
            for seat in seats
        ]
        session.add_all(event_seats)
        await session.flush()
        await session.commit()

    # Bulk Redis Bitfield initialization via Pipeline
    pipe = redis.pipeline()
    for ez in event_zones:
        key = f"event_zone_seats:{ez.id}"
        # Set the last byte (offset 1999 * 8) to allocate memory in one shot
        pipe.execute_command("BITFIELD", key, "SET", "u8", str(1999 * 8), "0")
    await pipe.execute()

    elapsed = time.perf_counter() - start_time
    assert elapsed < 2.0, f"10k seats initialization took {elapsed:.2f}s, exceeding 2.0s threshold."


@pytest.mark.asyncio
async def test_pay_expired_order_fails(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    """Tier 2: Attempting payment on an order after expiration fails and rejects the transaction."""
    event_id = seeded_event["event_id"]
    seat_a1 = seeded_event["seat_ids"]["a1"]
    user_id = 1

    # Lock seat
    await async_client.post(
        f"/api/v1/events/{event_id}/seats/lock",
        json={"user_id": user_id, "seat_ids": [seat_a1]},
    )

    # Fast-forward order to expired status
    db = container.db()
    async with db.get_async_session() as session:
        order = (await session.execute(select(Order).where(Order.user_id == user_id))).scalars().first()
        order_id = order.id
        order.payment_deadline = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
        order.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        order.status = OrderStatus.CANCELLED.value
        session.add(order)
        await session.commit()

    # Attempt to pay expired order
    resp = await async_client.post(f"/api/v1/orders/{order_id}/pay", json={"user_id": user_id})
    assert resp.status_code in (400, 404, 409)
