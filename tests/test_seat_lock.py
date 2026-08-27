import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from sqlmodel import select, text
from app.containers import Container
from app.enums import SeatStatus, OrderStatus, RedisSeatStatus

from app.models import Venue, Zone, Seat, Event, EventZone, EventSeat, User, Order, OrderTicket

@pytest.fixture
def container():
    """Create DI Container instance."""
    return Container()

@pytest.fixture
def seat_cache(container):
    return container.event_lock_cache()

@pytest.fixture
def seat_service(container):
    return container.event_service()

@pytest.fixture
def event_repo(container):
    return container.event_repository()

@pytest.fixture
def order_repo(container):
    return container.order_repository()

@pytest.fixture(autouse=True)
async def setup_test_data(container):
    """Automatically seed clean test data and clean up Redis before/after each test."""
    container.redis_client.reset()
    container.db.reset()

    redis_client = container.redis_client()
    keys = await redis_client.keys("event_zone_seats:*")
    keys.extend(await redis_client.keys("order_ttl:*"))
    for k in keys:
        await redis_client.delete(k)
    await redis_client.delete("order_delay_queue")

        
    db = container.db()
    async with db.get_async_session() as session:
        await session.execute(text('TRUNCATE TABLE order_ticket, "order", event_seat, event_zone, event, seat, zone, venue, "user" RESTART IDENTITY CASCADE;'))
        await session.commit()
        
    async with db.get_async_session() as session:
        # Create venue
        venue = Venue(name="Test Arena", description="Test Desc", address="Test Addr")
        session.add(venue)
        await session.flush()
        
        # Create zones
        zone_a = Zone(code="Test Zone A", venue_id=venue.id)
        session.add(zone_a)
        await session.flush()
        
        # Create seats
        seat_1 = Seat(serial_number=1, row=1, column=1, zone_id=zone_a.id)
        seat_2 = Seat(serial_number=2, row=1, column=2, zone_id=zone_a.id)
        session.add_all([seat_1, seat_2])
        await session.flush()
        
        # Create event
        start_time = datetime.now(timezone.utc) + timedelta(days=30)
        end_time = start_time + timedelta(hours=2)
        event = Event(
            name="Test Concert",
            description="Test Concert Desc",
            start_at=start_time.replace(tzinfo=None),
            end_at=str(end_time.replace(tzinfo=None)),
            venue_id=venue.id
        )
        session.add(event)
        await session.flush()
        
        # Create EventZone
        ez_a = EventZone(event_id=event.id, zone_id=zone_a.id, price=1000)
        session.add(ez_a)
        await session.flush()
        
        # Create EventSeat
        es_1 = EventSeat(event_zone_id=ez_a.id, seat_id=seat_1.id, status=SeatStatus.RELEASED)
        es_2 = EventSeat(event_zone_id=ez_a.id, seat_id=seat_2.id, status=SeatStatus.RELEASED)
        session.add_all([es_1, es_2])
        await session.flush()

        # Create users
        user_101 = User(id=101, name="User 101")
        user_102 = User(id=102, name="User 102")
        user_999 = User(id=999, name="User 999")
        session.add_all([user_101, user_102, user_999])
        await session.flush()
        await session.commit()

    yield

    # Clean Redis again
    keys = await redis_client.keys("event_zone_seats:*")
    keys.extend(await redis_client.keys("order_ttl:*"))
    for k in keys:
        await redis_client.delete(k)


@pytest.mark.asyncio
async def test_lock_seats_success(seat_service, container):
    """Verify that multiple available seats can be locked successfully."""
    # Find event and seats from DB
    db = container.db()
    async with db.get_async_session() as session:
        event = (await session.execute(select(Event))).scalars().first()
        seats = (await session.execute(select(Seat))).scalars().all()
        
    seat_ids = [s.id for s in seats]
    
    # Run lock seats
    success = await seat_service.lock_seats(event.id, seat_ids, user_id=999)
    assert success is True

    # Verify Redis status (both should be 1)
    async with db.get_async_session() as session:
        event_seats = (await session.execute(select(EventSeat))).scalars().all()
        order = (await session.execute(select(Order).where(Order.user_id == 999))).scalars().first()
        
    assert order is not None
    assert order.status == OrderStatus.PENDING.value
    assert order.checkout_amount == 2000 # 1000 * 2 seats

    # Verify Redis bitfield value
    for es in event_seats:
        assert es.status == SeatStatus.LOCK
        
    redis_client = container.redis_client()
    for es, seat in zip(event_seats, seats):
        key = f"event_zone_seats:{es.event_zone_id}"
        offset = seat.serial_number - 1
        res = await redis_client.execute_command("BITFIELD", key, "GET", "u8", str(offset * 8))
        assert res[0] == RedisSeatStatus.LOCKED.value


@pytest.mark.asyncio
async def test_lock_seats_conflict(seat_service, container):
    """Verify that lock fails if any of the seats is already locked."""
    db = container.db()
    async with db.get_async_session() as session:
        event = (await session.execute(select(Event))).scalars().first()
        seats = (await session.execute(select(Seat))).scalars().all()
        
    seat_ids = [s.id for s in seats]
    
    # User 1 locks seat 1
    success1 = await seat_service.lock_seats(event.id, [seat_ids[0]], user_id=101)
    assert success1 is True
    
    # User 2 tries to lock seat 1 and 2
    success2 = await seat_service.lock_seats(event.id, seat_ids, user_id=102)
    assert success2 is False # Should fail since seat 1 is already locked
    
    # Seat 2 should still be RELEASED (All-or-nothing rollback verified)
    async with db.get_async_session() as session:
        es_2 = (await session.execute(select(EventSeat).where(EventSeat.seat_id == seat_ids[1]))).scalars().first()
    assert es_2.status == SeatStatus.RELEASED

@pytest.mark.asyncio
async def test_unlock_seats_partial(seat_service, container):
    """Verify that seats can be partially unlocked and order amount adjusts accordingly."""
    db = container.db()
    async with db.get_async_session() as session:
        event = (await session.execute(select(Event))).scalars().first()
        seats = (await session.execute(select(Seat))).scalars().all()
    
    seat_ids = [s.id for s in seats]
    
    # Lock both seats
    await seat_service.lock_seats(event.id, seat_ids, user_id=101)
    
    # Unlock seat 1
    success = await seat_service.unlock_seats(event.id, [seat_ids[0]], user_id=101)
    assert success is True
    
    # Verify DB states
    async with db.get_async_session() as session:
        es_1 = (await session.execute(select(EventSeat).where(EventSeat.seat_id == seat_ids[0]))).scalars().first()
        es_2 = (await session.execute(select(EventSeat).where(EventSeat.seat_id == seat_ids[1]))).scalars().first()
        order = (await session.execute(select(Order).where(Order.user_id == 101))).scalars().first()
        
    assert es_1.status == SeatStatus.RELEASED
    assert es_2.status == SeatStatus.LOCK
    assert order.checkout_amount == 1000 # 2000 - 1000

@pytest.mark.asyncio
async def test_order_expiration_release(seat_service, container):
    """Verify that release_expired_orders successfully cancels expired pending orders and unlocks their seats."""
    db = container.db()
    async with db.get_async_session() as session:
        event = (await session.execute(select(Event))).scalars().first()
        seats = (await session.execute(select(Seat))).scalars().all()
        
    seat_ids = [s.id for s in seats]
    
    # Lock seats
    await seat_service.lock_seats(event.id, seat_ids, user_id=101)
    
    # Force the order's created_at and payment_deadline to be in the past
    async with db.get_async_session() as session:
        order = (await session.execute(select(Order).where(Order.user_id == 101))).scalars().first()
        order.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=11)
        order.payment_deadline = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        session.add(order)
        await session.commit()
        
        # Also update Redis ZSET score to match the past deadline for ZSET test coverage
        redis_client = container.redis_client()
        deadline_ts = order.payment_deadline.replace(tzinfo=timezone.utc).timestamp()
        await redis_client.zadd("order_delay_queue", {str(order.id): deadline_ts})

        
    # Trigger expiration release scan
    await seat_service.release_expired_orders()
    
    # Verify order soft deleted & seats released in DB
    async with db.get_async_session() as session:
        order = (await session.execute(select(Order).where(Order.user_id == 101))).scalars().first()
        event_seats = (await session.execute(select(EventSeat))).scalars().all()
        
    assert order.deleted_at is not None
    assert order.status == OrderStatus.CANCELLED.value
    for es in event_seats:
        assert es.status == SeatStatus.RELEASED
        
    # Verify Redis is reset to 0x00
    redis_client = container.redis_client()
    for es, seat in zip(event_seats, seats):
        key = f"event_zone_seats:{es.event_zone_id}"
        offset = seat.serial_number - 1
        res = await redis_client.execute_command("BITFIELD", key, "GET", "u8", str(offset * 8))
        assert res[0] == RedisSeatStatus.RELEASED.value

