import asyncio
from typing import AsyncGenerator

import pytest
from httpx import AsyncClient, ASGITransport
from sqlmodel import text

from app.containers import Container
from app.main import app
from app.models import Seat, User, Venue, Zone


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
def db_database(container: Container):
    """Provides the Database instance."""
    return container.db()


@pytest.fixture(autouse=True)
async def clean_state(container: Container) -> AsyncGenerator[None, None]:
    """Truncates every table before each test.

    No Redis teardown: the seat-status cache is not implemented yet.
    """
    container.db.reset()

    db = container.db()
    async with db.get_async_session() as session:
        await session.execute(
            text(
                'TRUNCATE TABLE order_ticket, "order", event_seat, event_zone, '
                'event, seat, zone, venue, "user" RESTART IDENTITY CASCADE;'
            )
        )
        await session.commit()

    yield


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client fixture simulating client requests against FastAPI application."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def seeded_venue_and_seats(container: Container) -> dict:
    """A venue with 2 zones and 2 seats each, plus two users."""
    db = container.db()
    async with db.get_async_session() as session:
        venue = Venue(name="Taipei Arena", description="Standard Arena", address="Taipei City")
        session.add(venue)
        await session.flush()

        zone_a = Zone(name="ZONE_A", venue_id=venue.id)
        zone_b = Zone(name="ZONE_B", venue_id=venue.id)
        session.add_all([zone_a, zone_b])
        await session.flush()

        seat_a1 = Seat(serial_number=1, row=1, col=1, zone_id=zone_a.id)
        seat_a2 = Seat(serial_number=2, row=1, col=2, zone_id=zone_a.id)
        seat_b1 = Seat(serial_number=1, row=1, col=1, zone_id=zone_b.id)
        seat_b2 = Seat(serial_number=2, row=1, col=2, zone_id=zone_b.id)
        session.add_all([seat_a1, seat_a2, seat_b1, seat_b2])
        await session.flush()

        user_1 = User(name="Alice", email="alice@example.com", hashed_password="x")
        user_2 = User(name="Bob", email="bob@example.com", hashed_password="x")
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
