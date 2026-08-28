import asyncio
from datetime import datetime, timezone, timedelta
import pytest
from httpx import AsyncClient, ASGITransport
from sqlmodel import select, text
from app.main import app
from app.containers import Container
from app.models import Venue, Zone, Seat, Event
from app.schemas import VenueCreate, ZoneCreate, SeatCreate


@pytest.fixture
def container() -> Container:
    """Provides the dependency injection Container instance."""
    return Container()


@pytest.fixture(autouse=True)
async def clean_state(container: Container):
    """Cleans PostgreSQL tables and resets connections before and after each test."""
    container.redis_client.reset()
    container.db.reset()

    db = container.db()
    async with db.get_async_session() as session:
        await session.execute(
            text(
                'TRUNCATE TABLE order_ticket, "order", event_seat, event_zone, event, seat, zone, venue, "user" '
                'RESTART IDENTITY CASCADE;'
            )
        )
        await session.commit()

    yield

    async with db.get_async_session() as session:
        await session.execute(
            text(
                'TRUNCATE TABLE order_ticket, "order", event_seat, event_zone, event, seat, zone, venue, "user" '
                'RESTART IDENTITY CASCADE;'
            )
        )
        await session.commit()


@pytest.fixture
async def async_client() -> AsyncClient:
    """Async HTTP client fixture simulating client requests against the FastAPI application."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_concurrent_duplicate_zone_creation_race_condition(async_client: AsyncClient, container: Container):
    """
    Stress Test 1: Concurrently attempt to create duplicate zone codes under the same venue.
    Oracle: Exactly 1 request succeeds (201 Created), and all remaining requests fail with 409 Conflict.
    Zero unhandled exceptions or duplicate records in DB.
    """
    # 1. Create Venue
    v_resp = await async_client.post(
        "/api/v1/venues",
        json={"name": "Race Arena", "description": "Concurrency Test", "address": "City Center"}
    )
    assert v_resp.status_code == 201
    venue_id = v_resp.json()["id"]

    # 2. Fire 10 concurrent requests creating the identical zone code
    CONCURRENT_TASKS = 10
    zone_payload = {"code": "ZONE_CONCURRENT_RACE"}

    async def create_single_zone():
        return await async_client.post(f"/api/v1/venues/{venue_id}/zones", json=zone_payload)

    responses = await asyncio.gather(*[create_single_zone() for _ in range(CONCURRENT_TASKS)])

    # 3. Verify status codes
    status_codes = [r.status_code for r in responses]
    success_count = status_codes.count(201)
    conflict_count = status_codes.count(409)

    assert success_count == 1, f"Expected exactly 1 success, got {success_count}. Statuses: {status_codes}"
    assert conflict_count == CONCURRENT_TASKS - 1, f"Expected {CONCURRENT_TASKS - 1} conflicts, got {conflict_count}"
    assert set(status_codes) == {201, 409}, f"Unexpected status codes returned: {status_codes}"

    # 4. Verify DB consistency
    db = container.db()
    async with db.get_async_session() as session:
        zones = (
            await session.execute(
                select(Zone).where(Zone.venue_id == venue_id).where(Zone.deleted_at == None)
            )
        ).scalars().all()
        assert len(zones) == 1
        assert zones[0].code == "ZONE_CONCURRENT_RACE"


@pytest.mark.asyncio
async def test_concurrent_duplicate_seat_creation_race_condition(async_client: AsyncClient, container: Container):
    """
    Stress Test 2: Concurrently attempt to create duplicate seat serial numbers in the same zone.
    Oracle: Exactly 1 request succeeds (201 Created), and all remaining requests fail with 409 Conflict.
    """
    # 1. Setup Venue and Zone
    v_resp = await async_client.post(
        "/api/v1/venues",
        json={"name": "Seat Race Arena", "description": "Seat Race", "address": "Arena Road"}
    )
    venue_id = v_resp.json()["id"]
    z_resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json={"code": "ZONE_VIP"})
    zone_id = z_resp.json()["id"]

    # 2. Fire 10 concurrent requests creating the identical seat serial number
    CONCURRENT_TASKS = 10
    seat_payload = {"serial_number": 88, "row": 1, "column": 88}

    async def create_single_seat():
        return await async_client.post(f"/api/v1/zones/{zone_id}/seats", json=seat_payload)

    responses = await asyncio.gather(*[create_single_seat() for _ in range(CONCURRENT_TASKS)])

    status_codes = [r.status_code for r in responses]
    success_count = status_codes.count(201)
    conflict_count = status_codes.count(409)

    assert success_count == 1, f"Expected exactly 1 success, got {success_count}. Statuses: {status_codes}"
    assert conflict_count == CONCURRENT_TASKS - 1, f"Expected {CONCURRENT_TASKS - 1} conflicts, got {conflict_count}"
    assert set(status_codes) == {201, 409}

    # 3. Verify DB state
    db = container.db()
    async with db.get_async_session() as session:
        seats = (
            await session.execute(
                select(Seat).where(Seat.zone_id == zone_id).where(Seat.deleted_at == None)
            )
        ).scalars().all()
        assert len(seats) == 1
        assert seats[0].serial_number == 88


@pytest.mark.asyncio
async def test_batch_seat_creation_internal_duplicate_atomicity(async_client: AsyncClient, container: Container):
    """
    Stress Test 3: Batch seat creation with duplicate serial numbers within the payload.
    Oracle: Request must be rejected with 409 Conflict.
    Atomicity check: Zero seats from the payload must be persisted to DB.
    """
    v_resp = await async_client.post(
        "/api/v1/venues",
        json={"name": "Batch Arena", "description": "Batch Test", "address": "Batch Way"}
    )
    venue_id = v_resp.json()["id"]
    z_resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json={"code": "ZONE_BATCH"})
    zone_id = z_resp.json()["id"]

    # Payload with duplicate serial_number 2
    batch_payload = {
        "seats": [
            {"serial_number": 1, "row": 1, "column": 1},
            {"serial_number": 2, "row": 1, "column": 2},
            {"serial_number": 3, "row": 1, "column": 3},
            {"serial_number": 2, "row": 1, "column": 4},  # duplicate!
            {"serial_number": 4, "row": 1, "column": 5},
        ]
    }
    resp = await async_client.post(f"/api/v1/zones/{zone_id}/seats/batch", json=batch_payload)
    assert resp.status_code == 409

    # Verify zero seats persisted
    db = container.db()
    async with db.get_async_session() as session:
        seats = (
            await session.execute(
                select(Seat).where(Seat.zone_id == zone_id).where(Seat.deleted_at == None)
            )
        ).scalars().all()
        assert len(seats) == 0, f"Expected 0 seats due to atomic rollback, found {len(seats)}"


@pytest.mark.asyncio
async def test_batch_seat_creation_collision_against_existing_seats_atomicity(
    async_client: AsyncClient, container: Container
):
    """
    Stress Test 4: Batch seat creation where one seat collides with a pre-existing seat in DB.
    Oracle: Request must be rejected with 409 Conflict.
    Atomicity check: Non-colliding seats in the batch must NOT be partially persisted (all-or-nothing rollback).
    """
    v_resp = await async_client.post(
        "/api/v1/venues",
        json={"name": "Collision Arena", "description": "Collision Test", "address": "Collision Way"}
    )
    venue_id = v_resp.json()["id"]
    z_resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json={"code": "ZONE_COLLISION"})
    zone_id = z_resp.json()["id"]

    # Pre-create seat 3
    pre_resp = await async_client.post(
        f"/api/v1/zones/{zone_id}/seats",
        json={"serial_number": 3, "row": 1, "column": 3}
    )
    assert pre_resp.status_code == 201

    # Attempt batch creation of seats 1, 2, 3, 4, 5 (seat 3 collides)
    batch_payload = {
        "seats": [
            {"serial_number": 1, "row": 1, "column": 1},
            {"serial_number": 2, "row": 1, "column": 2},
            {"serial_number": 3, "row": 1, "column": 3},  # collides with existing
            {"serial_number": 4, "row": 1, "column": 4},
            {"serial_number": 5, "row": 1, "column": 5},
        ]
    }
    batch_resp = await async_client.post(f"/api/v1/zones/{zone_id}/seats/batch", json=batch_payload)
    assert batch_resp.status_code == 409

    # Verify that only the pre-existing seat 3 exists in DB; seats 1, 2, 4, 5 were NOT committed
    db = container.db()
    async with db.get_async_session() as session:
        seats = (
            await session.execute(
                select(Seat).where(Seat.zone_id == zone_id).where(Seat.deleted_at == None)
            )
        ).scalars().all()
        assert len(seats) == 1
        assert seats[0].serial_number == 3


@pytest.mark.asyncio
async def test_concurrent_overlapping_batch_seat_creation_race_condition(
    async_client: AsyncClient, container: Container
):
    """
    Stress Test 5: Multiple concurrent batch creation requests with overlapping seat serial numbers.
    Oracle: Requests must either succeed (201) or fail (409) cleanly with no 500s or DB corruption.
    The final DB state must contain strictly unique serial numbers without duplicates.
    """
    v_resp = await async_client.post(
        "/api/v1/venues",
        json={"name": "Overlap Batch Arena", "description": "Overlap Test", "address": "Overlap Way"}
    )
    venue_id = v_resp.json()["id"]
    z_resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json={"code": "ZONE_OVERLAP"})
    zone_id = z_resp.json()["id"]

    # 5 overlapping batches:
    # Batch A: 1..10
    # Batch B: 5..15 (overlaps A and C)
    # Batch C: 10..20 (overlaps A, B, D)
    # Batch D: 15..25 (overlaps B, C)
    # Batch E: 1..5 (overlaps A)
    batches = [
        [{"serial_number": i, "row": 1, "column": i} for i in range(1, 11)],
        [{"serial_number": i, "row": 1, "column": i} for i in range(5, 16)],
        [{"serial_number": i, "row": 1, "column": i} for i in range(10, 21)],
        [{"serial_number": i, "row": 1, "column": i} for i in range(15, 26)],
        [{"serial_number": i, "row": 1, "column": i} for i in range(1, 6)],
    ]

    async def post_batch(b):
        return await async_client.post(f"/api/v1/zones/{zone_id}/seats/batch", json={"seats": b})

    responses = await asyncio.gather(*[post_batch(b) for b in batches])
    status_codes = [r.status_code for r in responses]

    # Verify no server crashes
    assert all(code in (201, 409) for code in status_codes), f"Unexpected status codes: {status_codes}"
    assert 201 in status_codes, "At least one batch should have succeeded"

    # Verify DB state: All serial numbers must be strictly unique
    db = container.db()
    async with db.get_async_session() as session:
        seats = (
            await session.execute(
                select(Seat).where(Seat.zone_id == zone_id).where(Seat.deleted_at == None)
            )
        ).scalars().all()

        serial_nums = [s.serial_number for s in seats]
        assert len(serial_nums) == len(set(serial_nums)), f"Duplicate serial numbers found in DB: {serial_nums}"


@pytest.mark.asyncio
async def test_high_volume_batch_seat_creation(async_client: AsyncClient, container: Container):
    """
    Stress Test 6: High-volume batch creation of 500 seats at once.
    Oracle: Returns 201 Created, exactly 500 seats persisted, order and serial numbers verified.
    """
    v_resp = await async_client.post(
        "/api/v1/venues",
        json={"name": "High Volume Arena", "description": "500 Seats", "address": "Mega Complex"}
    )
    venue_id = v_resp.json()["id"]
    z_resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json={"code": "ZONE_MEGA"})
    zone_id = z_resp.json()["id"]

    TOTAL_SEATS = 500
    batch_payload = {
        "seats": [
            {"serial_number": i, "row": (i - 1) // 50 + 1, "column": (i - 1) % 50 + 1}
            for i in range(1, TOTAL_SEATS + 1)
        ]
    }

    batch_resp = await async_client.post(f"/api/v1/zones/{zone_id}/seats/batch", json=batch_payload)
    assert batch_resp.status_code == 201
    created_seats = batch_resp.json()
    assert len(created_seats) == TOTAL_SEATS

    # Verify GET list
    list_resp = await async_client.get(f"/api/v1/zones/{zone_id}/seats")
    assert list_resp.status_code == 200
    listed_seats = list_resp.json()
    assert len(listed_seats) == TOTAL_SEATS
    assert [s["serial_number"] for s in listed_seats] == list(range(1, TOTAL_SEATS + 1))


@pytest.mark.asyncio
async def test_cascade_soft_delete_with_deep_hierarchy(async_client: AsyncClient, container: Container):
    """
    Stress Test 7: Cascade soft delete on a large venue with multiple zones and 100 seats.
    Oracle: Soft delete marks venue, all zones, and all seats as deleted_at IS NOT NULL.
    All subsequent GET requests return 404 Not Found.
    """
    # 1. Create Venue
    v_resp = await async_client.post(
        "/api/v1/venues",
        json={"name": "Deep Hierarchy Arena", "description": "Testing deep cascade", "address": "South Gate"}
    )
    venue_id = v_resp.json()["id"]

    # 2. Create 3 Zones
    zone_ids = []
    for code in ["ZONE_1", "ZONE_2", "ZONE_3"]:
        z_resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json={"code": code})
        zone_ids.append(z_resp.json()["id"])

    # 3. Create seats under each zone (30 seats each = 90 total)
    for z_id in zone_ids:
        batch_payload = {
            "seats": [
                {"serial_number": i, "row": 1, "column": i}
                for i in range(1, 31)
            ]
        }
        b_resp = await async_client.post(f"/api/v1/zones/{z_id}/seats/batch", json=batch_payload)
        assert b_resp.status_code == 201

    # 4. Soft delete venue
    del_resp = await async_client.delete(f"/api/v1/venues/{venue_id}")
    assert del_resp.status_code == 200

    # 5. Verify all GET endpoints return 404
    assert (await async_client.get(f"/api/v1/venues/{venue_id}")).status_code == 404
    for z_id in zone_ids:
        assert (await async_client.get(f"/api/v1/zones/{z_id}")).status_code == 404
        assert (await async_client.get(f"/api/v1/venues/{venue_id}/zones")).status_code == 404

    # 6. Verify in DB directly: deleted_at populated on all 1 venue, 3 zones, 90 seats
    db = container.db()
    async with db.get_async_session() as session:
        v_db = (await session.execute(select(Venue).where(Venue.id == venue_id))).scalars().first()
        z_db = (await session.execute(select(Zone).where(Zone.venue_id == venue_id))).scalars().all()
        s_db = (await session.execute(select(Seat).where(Seat.zone_id.in_(zone_ids)))).scalars().all()

        assert v_db.deleted_at is not None
        assert len(z_db) == 3
        assert all(z.deleted_at is not None for z in z_db)
        assert len(s_db) == 90
        assert all(s.deleted_at is not None for s in s_db)
