import asyncio
from typing import AsyncGenerator

import pytest
from httpx import AsyncClient, ASGITransport
from sqlmodel import text

from app.containers import Container
from app.enums import ManagerRole, OrderStatus, PaymentStatus
from app.libs.clock.functional import now_utc
from app.main import app
from app.models import Customer, Order, Seat, Manager, Venue, Zone
from app.schemas.event import EventCreate


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
    """Truncates every table and flushes Redis before each test.

    A leftover sold bit makes the next test fail to lock a seat it never
    touched. tests/conftest.py is what keeps this off the developer's stack.
    """
    container.db.reset()

    db = container.db()
    async with db.get_async_session() as session:
        await session.execute(
            text(
                'TRUNCATE TABLE order_ticket, "order", event_seat, event_zone, '
                'event, seat, zone, venue, customer, manager RESTART IDENTITY CASCADE;'
            )
        )
        await session.commit()

    await container.redis_client().flushdb()

    yield


async def _save(container: Container, account: Customer | Manager) -> int:
    async with container.db().get_async_session() as session:
        session.add(account)
        await session.commit()
        return account.id


@pytest.fixture
def customer_headers(container: Container):
    """Creates a customer and returns a bearer header for it."""

    async def _headers(email: str = "customer@example.com") -> dict[str, str]:
        customer_id = await _save(container, Customer(name="customer", email=email))
        return {"Authorization": f"Bearer {container.access_token_codec().encode_customer(customer_id)}"}

    return _headers


@pytest.fixture
def manager_headers(container: Container):
    """Creates a manager account of the given role and returns a bearer header for it."""

    async def _headers(role: ManagerRole = ManagerRole.MANAGER, email: str | None = None) -> dict[str, str]:
        account = Manager(
            name=role.lower(), email=email or f"{role.lower()}@example.com", hashed_password="x", role=role
        )
        manager_id = await _save(container, account)
        return {"Authorization": f"Bearer {container.access_token_codec().encode_manager(manager_id)}"}

    return _headers


@pytest.fixture
async def anonymous_client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def async_client(manager_headers) -> AsyncGenerator[AsyncClient, None]:
    """Signed in as a manager, since most tests drive the event editing routes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers=await manager_headers(email="fixture-manager@example.com"),
    ) as client:
        yield client


@pytest.fixture
async def seeded_venue_and_seats(container: Container) -> dict:
    """A venue with 2 zones and 2 seats each, plus two customers."""
    db = container.db()
    async with db.get_async_session() as session:
        venue = Venue(name="Taipei Arena", description="Standard Arena", address="Taipei City")
        session.add(venue)
        await session.flush()

        zone_a = Zone(name="ZONE_A", venue_id=venue.id)
        zone_b = Zone(name="ZONE_B", venue_id=venue.id)
        session.add_all([zone_a, zone_b])
        await session.flush()

        seat_a1 = Seat(serial_number=0, row=1, col=1, zone_id=zone_a.id)
        seat_a2 = Seat(serial_number=1, row=1, col=2, zone_id=zone_a.id)
        seat_b1 = Seat(serial_number=0, row=1, col=1, zone_id=zone_b.id)
        seat_b2 = Seat(serial_number=1, row=1, col=2, zone_id=zone_b.id)
        session.add_all([seat_a1, seat_a2, seat_b1, seat_b2])
        await session.flush()

        customer_1 = Customer(name="Alice", email="alice@example.com")
        customer_2 = Customer(name="Bob", email="bob@example.com")
        session.add_all([customer_1, customer_2])
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
            "customers": [customer_1.id, customer_2.id],
        }


@pytest.fixture
async def seeded_event(container: Container, seeded_venue_and_seats: dict) -> dict:
    """An unpublished event cloned from the 2-zone / 4-seat venue.

    Every zone price starts unset, so the event cannot be published as-is.
    """
    from datetime import timedelta

    repo = container.event_repository()
    start_at = now_utc() + timedelta(days=30)
    payload = EventCreate(
        name="World Tour 2026",
        description="Mega Live Concert",
        start_at=start_at,
        end_at=start_at + timedelta(hours=3),
        venue_id=seeded_venue_and_seats["venue_id"],
    )
    async with repo.create(payload) as event:
        layout = await event.layout()
        event_id = event.event.id

    return {
        "event_id": event_id,
        "zone_ids": {zone.zone.name: zone.zone.id for zone in layout.zones},
        "start_at": start_at,
        "customer_id": seeded_venue_and_seats["customers"][0],
        "venue_id": seeded_venue_and_seats["venue_id"],
    }


@pytest.fixture
def place_order(container: Container):
    """Inserts an order for an event, so the "no active order" rule can be exercised."""

    async def _place(event_id: int, customer_id: int, soft_deleted: bool = False,
                     status: OrderStatus = OrderStatus.PENDING) -> int:
        db = container.db()
        async with db.get_async_session() as session:
            order = Order(
                customer_id=customer_id,
                event_id=event_id,
                checkout_amount=0,
                status=status,
                payment_status=PaymentStatus.PENDING,
                deleted_at=now_utc() if soft_deleted else None,
            )
            session.add(order)
            await session.commit()
            return order.id

    return _place
