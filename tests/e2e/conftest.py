import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator
import httpx
from httpx import AsyncClient, ASGITransport
from sqlmodel import select, text
from app.main import app
from app.containers import Container
from app.models import Venue, Zone, Seat, Event, EventZone, EventSeat, User, Order, OrderTicket
from app.enums import SeatStatus, OrderStatus, PaymentStatus, RedisSeatStatus


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def container() -> Container:
    """Provides the dependency injection Container instance."""
    return Container()


@pytest.fixture
def redis_client(container: Container):
    """Provides the application Redis client instance."""
    return container.redis_client()


@pytest.fixture
def db_database(container: Container):
    """Provides the Database instance."""
    return container.db()


@pytest.fixture(autouse=True)
async def clean_state(container: Container) -> AsyncGenerator[None, None]:
    """Ensures PostgreSQL tables and Redis keys are completely cleaned before and after each test."""
    container.redis_client.reset()
    container.db.reset()

    redis = container.redis_client()
    keys = await redis.keys("event_zone_seats:*")
    keys.extend(await redis.keys("order_ttl:*"))
    for k in keys:
        await redis.delete(k)
    await redis.delete("order_delay_queue")

    db = container.db()
    async with db.get_async_session() as session:
        await session.execute(
            text('TRUNCATE TABLE order_ticket, "order", event_seat, event_zone, event, seat, zone, venue, "user" RESTART IDENTITY CASCADE;')
        )
        await session.commit()

    yield

    # Teardown Redis keys
    keys = await redis.keys("event_zone_seats:*")
    keys.extend(await redis.keys("order_ttl:*"))
    for k in keys:
        await redis.delete(k)
    await redis.delete("order_delay_queue")


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client fixture simulating client requests against FastAPI application."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def seeded_venue_and_seats(container: Container) -> dict:
    """Helper fixture providing a standard venue with 2 zones and 4 seats."""
    db = container.db()
    async with db.get_async_session() as session:
        venue = Venue(name="Taipei Arena", description="Standard Arena", address="Taipei City")
        session.add(venue)
        await session.flush()

        zone_a = Zone(code="ZONE_A", venue_id=venue.id)
        zone_b = Zone(code="ZONE_B", venue_id=venue.id)
        session.add_all([zone_a, zone_b])
        await session.flush()

        seat_a1 = Seat(serial_number=1, row=1, column=1, zone_id=zone_a.id)
        seat_a2 = Seat(serial_number=2, row=1, column=2, zone_id=zone_a.id)
        seat_b1 = Seat(serial_number=1, row=1, column=1, zone_id=zone_b.id)
        seat_b2 = Seat(serial_number=2, row=1, column=2, zone_id=zone_b.id)
        session.add_all([seat_a1, seat_a2, seat_b1, seat_b2])
        await session.flush()

        user_1 = User(id=1, name="Alice")
        user_2 = User(id=2, name="Bob")
        session.add_all([user_1, user_2])
        await session.flush()
        await session.commit()

        return {
            "venue_id": venue.id,
            "zone_a_id": zone_a.id,
            "zone_b_id": zone_b.id,
            "seats": {
                "a1": seat_a1.id,
                "a2": seat_a2.id,
                "b1": seat_b1.id,
                "b2": seat_b2.id,
            },
            "users": [user_1.id, user_2.id],
        }


@pytest.fixture
async def seeded_event(container: Container, seeded_venue_and_seats: dict) -> dict:
    """Helper fixture creating an event with initialized EventZone and EventSeat records."""
    db = container.db()
    venue_id = seeded_venue_and_seats["venue_id"]
    zone_a_id = seeded_venue_and_seats["zone_a_id"]
    zone_b_id = seeded_venue_and_seats["zone_b_id"]
    seats = seeded_venue_and_seats["seats"]

    async with db.get_async_session() as session:
        start_time = datetime.now(timezone.utc) + timedelta(days=30)
        end_time = start_time + timedelta(hours=3)
        event = Event(
            name="World Tour 2026",
            description="Mega Live Concert",
            start_at=start_time.replace(tzinfo=None),
            end_at=str(end_time.replace(tzinfo=None)),
            venue_id=venue_id,
        )
        session.add(event)
        await session.flush()

        ez_a = EventZone(event_id=event.id, zone_id=zone_a_id, price=3200)
        ez_b = EventZone(event_id=event.id, zone_id=zone_b_id, price=1800)
        session.add_all([ez_a, ez_b])
        await session.flush()

        es_a1 = EventSeat(event_zone_id=ez_a.id, seat_id=seats["a1"], status=SeatStatus.RELEASED)
        es_a2 = EventSeat(event_zone_id=ez_a.id, seat_id=seats["a2"], status=SeatStatus.RELEASED)
        es_b1 = EventSeat(event_zone_id=ez_b.id, seat_id=seats["b1"], status=SeatStatus.RELEASED)
        es_b2 = EventSeat(event_zone_id=ez_b.id, seat_id=seats["b2"], status=SeatStatus.RELEASED)
        session.add_all([es_a1, es_a2, es_b1, es_b2])
        await session.flush()
        await session.commit()

        # Initialize Redis Bitfield for both zones
        redis = container.redis_client()
        await redis.execute_command("BITFIELD", f"event_zone_seats:{ez_a.id}", "SET", "u8", "0", "0", "SET", "u8", "8", "0")
        await redis.execute_command("BITFIELD", f"event_zone_seats:{ez_b.id}", "SET", "u8", "0", "0", "SET", "u8", "8", "0")

        return {
            "event_id": event.id,
            "ez_a_id": ez_a.id,
            "ez_b_id": ez_b.id,
            "event_seats": {
                "a1": es_a1.id,
                "a2": es_a2.id,
                "b1": es_b1.id,
                "b2": es_b2.id,
            },
            "seat_ids": seats,
            "venue_id": venue_id,
        }
