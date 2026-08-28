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
async def test_create_venue_success(async_client: AsyncClient, container: Container):
    """Test 1: Verify creating a venue returns 201 Created and persists correctly to the database."""
    venue_payload = {
        "name": "Taipei Arena",
        "description": "Premier indoor venue in Taipei",
        "address": "Nanjing E. Rd., Taipei",
    }
    resp = await async_client.post("/api/v1/venues", json=venue_payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] is not None
    assert data["name"] == venue_payload["name"]
    assert data["description"] == venue_payload["description"]
    assert data["address"] == venue_payload["address"]
    assert data["deleted_at"] is None

    # Verify DB persistence
    db = container.db()
    async with db.get_async_session() as session:
        venue_db = (await session.execute(select(Venue).where(Venue.id == data["id"]))).scalars().first()
        assert venue_db is not None
        assert venue_db.name == venue_payload["name"]


@pytest.mark.asyncio
async def test_get_venues_and_detail(async_client: AsyncClient):
    """Test 2: Verify listing active venues and fetching specific venue details by ID."""
    # Create two venues
    venue1_payload = {"name": "Venue A", "description": "Desc A", "address": "Address A"}
    venue2_payload = {"name": "Venue B", "description": "Desc B", "address": "Address B"}

    resp1 = await async_client.post("/api/v1/venues", json=venue1_payload)
    resp2 = await async_client.post("/api/v1/venues", json=venue2_payload)
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    v1_id = resp1.json()["id"]
    v2_id = resp2.json()["id"]

    # List all venues
    list_resp = await async_client.get("/api/v1/venues")
    assert list_resp.status_code == 200
    venues = list_resp.json()
    assert len(venues) >= 2
    venue_ids = [v["id"] for v in venues]
    assert v1_id in venue_ids
    assert v2_id in venue_ids

    # Get single venue detail
    detail_resp = await async_client.get(f"/api/v1/venues/{v1_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["id"] == v1_id
    assert detail["name"] == "Venue A"

    # Non-existent venue returns 404
    missing_resp = await async_client.get("/api/v1/venues/99999")
    assert missing_resp.status_code == 404


@pytest.mark.asyncio
async def test_create_zone_success_and_conflict(async_client: AsyncClient):
    """Test 3: Verify zone creation under venue (201) and composite unique constraint violation returns 409 Conflict."""
    # 1. Create Venue
    venue_payload = {"name": "Kaohsiung Stadium", "description": "Main Stadium", "address": "Kaohsiung"}
    v_resp = await async_client.post("/api/v1/venues", json=venue_payload)
    assert v_resp.status_code == 201
    venue_id = v_resp.json()["id"]

    # 2. Create Zone A
    zone_payload = {"code": "ZONE_VIP"}
    z_resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json=zone_payload)
    assert z_resp.status_code == 201
    zone_data = z_resp.json()
    assert zone_data["code"] == "ZONE_VIP"
    assert zone_data["venue_id"] == venue_id

    # 3. Create Zone with duplicate code under same venue -> 409 Conflict
    dup_resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json=zone_payload)
    assert dup_resp.status_code == 409

    # 4. List zones under venue
    list_z_resp = await async_client.get(f"/api/v1/venues/{venue_id}/zones")
    assert list_z_resp.status_code == 200
    zones = list_z_resp.json()
    assert len(zones) == 1
    assert zones[0]["code"] == "ZONE_VIP"

    # 5. Get single zone by ID
    z_id = zone_data["id"]
    get_z_resp = await async_client.get(f"/api/v1/zones/{z_id}")
    assert get_z_resp.status_code == 200
    assert get_z_resp.json()["id"] == z_id

    # 6. Create zone under non-existent venue -> 404
    missing_venue_resp = await async_client.post("/api/v1/venues/99999/zones", json=zone_payload)
    assert missing_venue_resp.status_code == 404


@pytest.mark.asyncio
async def test_create_seat_success_and_conflict(async_client: AsyncClient):
    """Test 4: Verify seat creation (single and batch) and duplicate serial_number returns 409 Conflict."""
    # Setup Venue and Zone
    v_resp = await async_client.post(
        "/api/v1/venues",
        json={"name": "Taichung Dome", "description": "Central Venue", "address": "Taichung"},
    )
    venue_id = v_resp.json()["id"]
    z_resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json={"code": "ROCK_A"})
    zone_id = z_resp.json()["id"]

    # 1. Create single Seat
    seat_payload = {"serial_number": 1, "row": 1, "column": 1}
    s_resp = await async_client.post(f"/api/v1/zones/{zone_id}/seats", json=seat_payload)
    assert s_resp.status_code == 201
    seat_data = s_resp.json()
    assert seat_data["serial_number"] == 1
    assert seat_data["zone_id"] == zone_id

    # 2. Create duplicate seat in same zone -> 409 Conflict
    dup_s_resp = await async_client.post(f"/api/v1/zones/{zone_id}/seats", json=seat_payload)
    assert dup_s_resp.status_code == 409

    # 3. Batch create Seats
    batch_payload = {
        "seats": [
            {"serial_number": 2, "row": 1, "column": 2},
            {"serial_number": 3, "row": 1, "column": 3},
        ]
    }
    batch_resp = await async_client.post(f"/api/v1/zones/{zone_id}/seats/batch", json=batch_payload)
    assert batch_resp.status_code == 201
    batch_seats = batch_resp.json()
    assert len(batch_seats) == 2

    # 4. Batch create with duplicate serial number in payload -> 409 Conflict
    invalid_batch_payload = {
        "seats": [
            {"serial_number": 4, "row": 1, "column": 4},
            {"serial_number": 4, "row": 1, "column": 5},
        ]
    }
    invalid_batch_resp = await async_client.post(f"/api/v1/zones/{zone_id}/seats/batch", json=invalid_batch_payload)
    assert invalid_batch_resp.status_code == 409

    # 5. List seats by zone
    list_seats_resp = await async_client.get(f"/api/v1/zones/{zone_id}/seats")
    assert list_seats_resp.status_code == 200
    seats = list_seats_resp.json()
    assert len(seats) == 3
    assert [s["serial_number"] for s in seats] == [1, 2, 3]

    # 6. Get single seat by ID
    s1_id = seat_data["id"]
    get_s_resp = await async_client.get(f"/api/v1/seats/{s1_id}")
    assert get_s_resp.status_code == 200
    assert get_s_resp.json()["id"] == s1_id


@pytest.mark.asyncio
async def test_cascade_soft_delete_venue_success(async_client: AsyncClient, container: Container):
    """Test 5: Verify soft-deleting a venue sets deleted_at on venue, all its zones, and all its seats atomically."""
    # Setup hierarchy: Venue -> 2 Zones -> 4 Seats
    db = container.db()
    async with db.get_async_session() as session:
        venue = Venue(name="Cascade Stadium", description="Testing Cascade", address="Test City")
        session.add(venue)
        await session.flush()

        zone1 = Zone(code="ZONE_1", venue_id=venue.id)
        zone2 = Zone(code="ZONE_2", venue_id=venue.id)
        session.add_all([zone1, zone2])
        await session.flush()

        seat1 = Seat(serial_number=1, row=1, column=1, zone_id=zone1.id)
        seat2 = Seat(serial_number=2, row=1, column=2, zone_id=zone1.id)
        seat3 = Seat(serial_number=1, row=1, column=1, zone_id=zone2.id)
        seat4 = Seat(serial_number=2, row=1, column=2, zone_id=zone2.id)
        session.add_all([seat1, seat2, seat3, seat4])
        await session.flush()
        await session.commit()

        venue_id = venue.id
        zone1_id = zone1.id
        zone2_id = zone2.id

    # Execute soft delete via API
    del_resp = await async_client.delete(f"/api/v1/venues/{venue_id}")
    assert del_resp.status_code == 200

    # Verify all records have deleted_at populated in DB
    async with db.get_async_session() as session:
        v_db = (await session.execute(select(Venue).where(Venue.id == venue_id))).scalars().first()
        z_db = (await session.execute(select(Zone).where(Zone.venue_id == venue_id))).scalars().all()
        s_db = (
            (await session.execute(select(Seat).where(Seat.zone_id.in_([zone1_id, zone2_id]))))
            .scalars()
            .all()
        )

        assert v_db.deleted_at is not None
        assert len(z_db) == 2
        assert all(z.deleted_at is not None for z in z_db)
        assert len(s_db) == 4
        assert all(s.deleted_at is not None for s in s_db)


@pytest.mark.asyncio
async def test_soft_deleted_entities_inaccessible(async_client: AsyncClient, container: Container):
    """Test 6: Verify that after soft deletion, GET queries return 404 Not Found for venue, zones, and seats."""
    # 1. Create Venue, Zone, Seat via API
    v_resp = await async_client.post(
        "/api/v1/venues", json={"name": "Inaccessible Venue", "address": "Ghost Town"}
    )
    venue_id = v_resp.json()["id"]

    z_resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json={"code": "GHOST_ZONE"})
    zone_id = z_resp.json()["id"]

    s_resp = await async_client.post(
        f"/api/v1/zones/{zone_id}/seats", json={"serial_number": 1, "row": 1, "column": 1}
    )
    seat_id = s_resp.json()["id"]

    # 2. Delete the venue
    del_resp = await async_client.delete(f"/api/v1/venues/{venue_id}")
    assert del_resp.status_code == 200

    # 3. Verify GET requests return 404
    get_v = await async_client.get(f"/api/v1/venues/{venue_id}")
    assert get_v.status_code == 404

    get_z = await async_client.get(f"/api/v1/zones/{zone_id}")
    assert get_z.status_code == 404

    get_s = await async_client.get(f"/api/v1/seats/{seat_id}")
    assert get_s.status_code == 404

    # 4. Verify list queries do not include deleted items
    list_v = await async_client.get("/api/v1/venues")
    assert list_v.status_code == 200
    assert venue_id not in [v["id"] for v in list_v.json()]


@pytest.mark.asyncio
async def test_delete_venue_with_active_events_blocked(async_client: AsyncClient, container: Container):
    """Test 7: Verify that if an active non-deleted event is linked to the venue, deletion is blocked with 400 Bad Request."""
    db = container.db()
    async with db.get_async_session() as session:
        # Create venue
        venue = Venue(name="Active Concert Venue", description="Has Live Show", address="Arena Way")
        session.add(venue)
        await session.flush()

        # Create active event linked to venue
        start_time = datetime.now(timezone.utc) + timedelta(days=10)
        end_time = start_time + timedelta(hours=3)
        event = Event(
            name="Mega Concert 2026",
            description="Live Event",
            start_at=start_time.replace(tzinfo=None),
            end_at=str(end_time.replace(tzinfo=None)),
            venue_id=venue.id,
            deleted_at=None,
        )
        session.add(event)
        await session.flush()
        await session.commit()
        venue_id = venue.id

    # Attempt to delete venue with active event -> Must return 400 Bad Request
    del_resp = await async_client.delete(f"/api/v1/venues/{venue_id}")
    assert del_resp.status_code == 400

    # Verify venue is NOT soft-deleted in DB
    async with db.get_async_session() as session:
        v_db = (await session.execute(select(Venue).where(Venue.id == venue_id))).scalars().first()
        assert v_db is not None
        assert v_db.deleted_at is None
