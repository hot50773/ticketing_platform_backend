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
    """Async HTTP client fixture simulating client requests against FastAPI application."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ============================================================================
# Task 1: Cascade Soft Deletion Atomicity across Complex Topologies
# ============================================================================

@pytest.mark.asyncio
async def test_cascade_soft_delete_complex_topology(async_client: AsyncClient, container: Container):
    """
    Stress-tests cascade soft deletion on a complex topology:
    - 5 Zones under target Venue, each with 100 Seats (total 500 seats).
    - 1 Unrelated Venue with 2 Zones and 50 Seats (should remain completely unaffected).
    - Verify all 500 seats and 5 zones under target venue are soft-deleted with exact timestamp.
    - Verify unrelated venue, its zones, and its seats remain active (deleted_at IS NULL).
    """
    db = container.db()

    # 1. Setup Target Venue (5 zones x 100 seats = 500 seats)
    async with db.get_async_session() as session:
        target_venue = Venue(name="Mega Arena 500", description="Massive venue", address="100 Grand Ave")
        unrelated_venue = Venue(name="Unrelated Stadium", description="Safe venue", address="200 Safe St")
        session.add_all([target_venue, unrelated_venue])
        await session.flush()

        target_venue_id = target_venue.id
        unrelated_venue_id = unrelated_venue.id

        target_zones = [Zone(code=f"ZONE_{i:02d}", venue_id=target_venue_id) for i in range(5)]
        unrelated_zones = [Zone(code=f"U_ZONE_{i:02d}", venue_id=unrelated_venue_id) for i in range(2)]
        session.add_all(target_zones + unrelated_zones)
        await session.flush()

        target_zone_ids = [z.id for z in target_zones]
        unrelated_zone_ids = [z.id for z in unrelated_zones]

        # 500 seats for target venue
        target_seats = []
        for zid in target_zone_ids:
            for s_idx in range(1, 101):
                target_seats.append(
                    Seat(serial_number=s_idx, row=(s_idx - 1) // 10 + 1, column=(s_idx - 1) % 10 + 1, zone_id=zid)
                )
        session.add_all(target_seats)

        # 50 seats for unrelated venue
        unrelated_seats = []
        for zid in unrelated_zone_ids:
            for s_idx in range(1, 26):
                unrelated_seats.append(
                    Seat(serial_number=s_idx, row=(s_idx - 1) // 5 + 1, column=(s_idx - 1) % 5 + 1, zone_id=zid)
                )
        session.add_all(unrelated_seats)

        await session.commit()

    # 2. Trigger soft deletion of target venue via API
    del_resp = await async_client.delete(f"/api/v1/venues/{target_venue_id}")
    assert del_resp.status_code == 200, f"Expected 200 OK, got {del_resp.status_code}: {del_resp.text}"
    assert del_resp.json() == {"success": True}

    # 3. Direct DB verification of soft-deleted state and timestamp consistency
    async with db.get_async_session() as session:
        # Verify target venue
        tv = (await session.execute(select(Venue).where(Venue.id == target_venue_id))).scalar_one()
        assert tv.deleted_at is not None, "Target venue deleted_at must be populated"
        deletion_ts = tv.deleted_at

        # Verify all 5 target zones
        tz_list = (await session.execute(select(Zone).where(Zone.venue_id == target_venue_id))).scalars().all()
        assert len(tz_list) == 5
        for z in tz_list:
            assert z.deleted_at is not None, f"Zone {z.id} deleted_at must not be None"
            assert z.deleted_at == deletion_ts, "Zone deletion timestamp must match venue deletion timestamp"

        # Verify all 500 target seats
        ts_list = (
            (await session.execute(select(Seat).where(Seat.zone_id.in_(target_zone_ids))))
            .scalars()
            .all()
        )
        assert len(ts_list) == 500
        for s in ts_list:
            assert s.deleted_at is not None, f"Seat {s.id} deleted_at must not be None"
            assert s.deleted_at == deletion_ts, "Seat deletion timestamp must match venue deletion timestamp"

        # Verify unrelated venue, zones, and seats are untouched
        uv = (await session.execute(select(Venue).where(Venue.id == unrelated_venue_id))).scalar_one()
        assert uv.deleted_at is None, "Unrelated venue must not be deleted"

        uz_list = (await session.execute(select(Zone).where(Zone.venue_id == unrelated_venue_id))).scalars().all()
        assert len(uz_list) == 2
        assert all(z.deleted_at is None for z in uz_list), "Unrelated zones must not be deleted"

        us_list = (
            (await session.execute(select(Seat).where(Seat.zone_id.in_(unrelated_zone_ids))))
            .scalars()
            .all()
        )
        assert len(us_list) == 50
        assert all(s.deleted_at is None for s in us_list), "Unrelated seats must not be deleted"


@pytest.mark.asyncio
async def test_cascade_soft_delete_idempotency_and_repeat_rejection(async_client: AsyncClient, container: Container):
    """
    Verifies that calling DELETE on a venue twice returns 404 on the second attempt
    and does not alter the original deletion timestamp.
    """
    v_resp = await async_client.post("/api/v1/venues", json={"name": "Double Delete Venue", "address": "123 Way"})
    assert v_resp.status_code == 201
    venue_id = v_resp.json()["id"]

    # First delete -> 200 OK
    resp1 = await async_client.delete(f"/api/v1/venues/{venue_id}")
    assert resp1.status_code == 200

    # Capture initial timestamp
    db = container.db()
    async with db.get_async_session() as session:
        v_db = (await session.execute(select(Venue).where(Venue.id == venue_id))).scalar_one()
        first_deleted_at = v_db.deleted_at
        assert first_deleted_at is not None

    # Second delete -> 404 Not Found
    resp2 = await async_client.delete(f"/api/v1/venues/{venue_id}")
    assert resp2.status_code == 404

    # Timestamp must remain unchanged
    async with db.get_async_session() as session:
        v_db = (await session.execute(select(Venue).where(Venue.id == venue_id))).scalar_one()
        assert v_db.deleted_at == first_deleted_at


# ============================================================================
# Task 2: Active Event Blocking Guard & Soft-Deleted Event Bypass
# ============================================================================

@pytest.mark.asyncio
async def test_active_event_blocking_guard_lifecycle(async_client: AsyncClient, container: Container):
    """
    Adversarial verification of the active event guard:
    1. Create Venue with 2 Zones and 4 Seats.
    2. Create an active Event linked to Venue (deleted_at IS NULL).
    3. DELETE Venue -> MUST return 400 Bad Request.
    4. Assert DB state: 0 records marked deleted (Venue, Zones, Seats, Event all deleted_at IS NULL).
    5. Create a second soft-deleted event on the same venue.
    6. DELETE Venue -> Still MUST return 400 Bad Request because the first event is active.
    7. Soft delete the first event (deleted_at = now).
    8. DELETE Venue -> MUST succeed with 200 OK.
    9. Assert DB state: Venue, Zones, and Seats are all soft-deleted.
    """
    db = container.db()

    # 1. Setup Venue, Zones, Seats
    async with db.get_async_session() as session:
        venue = Venue(name="Festival Park", description="Outdoor Grounds", address="100 Festival Way")
        session.add(venue)
        await session.flush()
        venue_id = venue.id

        z1 = Zone(code="EAST_LAWN", venue_id=venue_id)
        z2 = Zone(code="WEST_LAWN", venue_id=venue_id)
        session.add_all([z1, z2])
        await session.flush()
        z1_id = z1.id
        z2_id = z2.id

        s1 = Seat(serial_number=1, row=1, column=1, zone_id=z1_id)
        s2 = Seat(serial_number=2, row=1, column=2, zone_id=z1_id)
        s3 = Seat(serial_number=1, row=1, column=1, zone_id=z2_id)
        s4 = Seat(serial_number=2, row=1, column=2, zone_id=z2_id)
        session.add_all([s1, s2, s3, s4])
        await session.flush()

        # 2. Add Active Event
        now = datetime.now(timezone.utc)
        active_event = Event(
            name="Summer Rock Fest",
            description="Active Live Concert",
            start_at=(now + timedelta(days=7)).replace(tzinfo=None),
            end_at=str((now + timedelta(days=7, hours=4)).replace(tzinfo=None)),
            venue_id=venue_id,
            deleted_at=None,
        )
        session.add(active_event)
        await session.flush()
        await session.commit()
        active_event_id = active_event.id

    # 3. Attempt to delete Venue -> Must fail with 400 Bad Request
    del_resp1 = await async_client.delete(f"/api/v1/venues/{venue_id}")
    assert del_resp1.status_code == 400, f"Expected 400 Bad Request, got {del_resp1.status_code}: {del_resp1.text}"
    assert "active non-deleted events" in del_resp1.json()["detail"].lower()

    # 4. Verify 0 records deleted in database
    async with db.get_async_session() as session:
        v_check = (await session.execute(select(Venue).where(Venue.id == venue_id))).scalar_one()
        assert v_check.deleted_at is None, "Venue must not be soft-deleted when blocked"

        z_checks = (await session.execute(select(Zone).where(Zone.venue_id == venue_id))).scalars().all()
        assert all(z.deleted_at is None for z in z_checks), "Zones must not be soft-deleted when blocked"

        s_checks = (
            (await session.execute(select(Seat).where(Seat.zone_id.in_([z1_id, z2_id]))))
            .scalars()
            .all()
        )
        assert all(s.deleted_at is None for s in s_checks), "Seats must not be soft-deleted when blocked"

    # 5. Add a second, already soft-deleted event
    async with db.get_async_session() as session:
        archived_event = Event(
            name="Winter Jazz Fest (Cancelled)",
            description="Past or cancelled event",
            start_at=(now - timedelta(days=30)).replace(tzinfo=None),
            end_at=str((now - timedelta(days=30, hours=-3)).replace(tzinfo=None)),
            venue_id=venue_id,
            deleted_at=(now - timedelta(days=29)).replace(tzinfo=None),
        )
        session.add(archived_event)
        await session.commit()

    # 6. Attempt delete again -> Still must fail with 400 because active_event is still active
    del_resp2 = await async_client.delete(f"/api/v1/venues/{venue_id}")
    assert del_resp2.status_code == 400

    # 7. Soft delete the active event
    async with db.get_async_session() as session:
        ev = (await session.execute(select(Event).where(Event.id == active_event_id))).scalar_one()
        ev.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(ev)
        await session.commit()

    # 8. Now delete venue -> Must succeed with 200 OK
    del_resp3 = await async_client.delete(f"/api/v1/venues/{venue_id}")
    assert del_resp3.status_code == 200, f"Expected 200 OK after event soft-deletion, got {del_resp3.status_code}: {del_resp3.text}"
    assert del_resp3.json() == {"success": True}

    # 9. Verify Venue, Zones, Seats are now soft-deleted
    async with db.get_async_session() as session:
        v_final = (await session.execute(select(Venue).where(Venue.id == venue_id))).scalar_one()
        assert v_final.deleted_at is not None

        z_finals = (await session.execute(select(Zone).where(Zone.venue_id == venue_id))).scalars().all()
        assert all(z.deleted_at is not None for z in z_finals)

        s_finals = (
            (await session.execute(select(Seat).where(Seat.zone_id.in_([z1_id, z2_id]))))
            .scalars()
            .all()
        )
        assert all(s.deleted_at is not None for s in s_finals)


# ============================================================================
# Task 3: Soft Delete Read Isolation across All Endpoints
# ============================================================================

@pytest.mark.asyncio
async def test_soft_delete_read_isolation_comprehensive(async_client: AsyncClient, container: Container):
    """
    Validates complete read isolation across ALL Venue/Zone/Seat endpoints:
    - GET /api/v1/venues (list) -> excludes soft-deleted venue
    - GET /api/v1/venues/{venue_id} -> returns 404 for soft-deleted venue
    - GET /api/v1/venues/{venue_id}/zones -> returns 404 for soft-deleted venue
    - GET /api/v1/zones/{zone_id} -> returns 404 for soft-deleted zone
    - GET /api/v1/zones/{zone_id}/seats -> returns 404 for soft-deleted zone
    - GET /api/v1/seats/{seat_id} -> returns 404 for soft-deleted seat
    - POST /api/v1/venues/{venue_id}/zones -> returns 404 when venue is soft-deleted
    - POST /api/v1/zones/{zone_id}/seats -> returns 404 when zone is soft-deleted
    - POST /api/v1/zones/{zone_id}/seats/batch -> returns 404 when zone is soft-deleted
    """
    # 1. Create Venue A (will remain active) and Venue B (will be deleted)
    va_resp = await async_client.post("/api/v1/venues", json={"name": "Active Venue A", "address": "100 Active Way"})
    assert va_resp.status_code == 201
    va_id = va_resp.json()["id"]

    vb_resp = await async_client.post("/api/v1/venues", json={"name": "Doomed Venue B", "address": "200 Delete Ave"})
    assert vb_resp.status_code == 201
    vb_id = vb_resp.json()["id"]

    # 2. Add Zone and Seat under Venue A
    za_resp = await async_client.post(f"/api/v1/venues/{va_id}/zones", json={"code": "ZONE_A1"})
    assert za_resp.status_code == 201
    za_id = za_resp.json()["id"]

    sa_resp = await async_client.post(
        f"/api/v1/zones/{za_id}/seats", json={"serial_number": 1, "row": 1, "column": 1}
    )
    assert sa_resp.status_code == 201
    sa_id = sa_resp.json()["id"]

    # 3. Add Zone and Seat under Venue B
    zb_resp = await async_client.post(f"/api/v1/venues/{vb_id}/zones", json={"code": "ZONE_B1"})
    assert zb_resp.status_code == 201
    zb_id = zb_resp.json()["id"]

    sb_resp = await async_client.post(
        f"/api/v1/zones/{zb_id}/seats", json={"serial_number": 1, "row": 1, "column": 1}
    )
    assert sb_resp.status_code == 201
    sb_id = sb_resp.json()["id"]

    # 4. Delete Venue B
    del_resp = await async_client.delete(f"/api/v1/venues/{vb_id}")
    assert del_resp.status_code == 200

    # 5. Verify GET /venues list isolation
    list_v = await async_client.get("/api/v1/venues")
    assert list_v.status_code == 200
    returned_v_ids = [v["id"] for v in list_v.json()]
    assert va_id in returned_v_ids, "Active Venue A must be listed"
    assert vb_id not in returned_v_ids, "Soft-deleted Venue B must NOT be listed"

    # 6. Verify GET single venue isolation
    get_va = await async_client.get(f"/api/v1/venues/{va_id}")
    assert get_va.status_code == 200

    get_vb = await async_client.get(f"/api/v1/venues/{vb_id}")
    assert get_vb.status_code == 404, "GET soft-deleted venue must return 404"

    # 7. Verify GET zones under venue isolation
    get_za_list = await async_client.get(f"/api/v1/venues/{va_id}/zones")
    assert get_za_list.status_code == 200
    assert len(get_za_list.json()) == 1

    get_zb_list = await async_client.get(f"/api/v1/venues/{vb_id}/zones")
    assert get_zb_list.status_code == 404, "GET zones for soft-deleted venue must return 404"

    # 8. Verify GET single zone isolation
    get_za = await async_client.get(f"/api/v1/zones/{za_id}")
    assert get_za.status_code == 200

    get_zb = await async_client.get(f"/api/v1/zones/{zb_id}")
    assert get_zb.status_code == 404, "GET soft-deleted zone must return 404"

    # 9. Verify GET seats under zone isolation
    get_sa_list = await async_client.get(f"/api/v1/zones/{za_id}/seats")
    assert get_sa_list.status_code == 200
    assert len(get_sa_list.json()) == 1

    get_sb_list = await async_client.get(f"/api/v1/zones/{zb_id}/seats")
    assert get_sb_list.status_code == 404, "GET seats for soft-deleted zone must return 404"

    # 10. Verify GET single seat isolation
    get_sa = await async_client.get(f"/api/v1/seats/{sa_id}")
    assert get_sa.status_code == 200

    get_sb = await async_client.get(f"/api/v1/seats/{sb_id}")
    assert get_sb.status_code == 404, "GET soft-deleted seat must return 404"

    # 11. Verify POST operations against soft-deleted parent entities return 404
    # Attempt creating zone under soft-deleted venue
    post_zone_del = await async_client.post(f"/api/v1/venues/{vb_id}/zones", json={"code": "ZONE_B2"})
    assert post_zone_del.status_code == 404, "Creating zone under deleted venue must return 404"

    # Attempt creating single seat under soft-deleted zone
    post_seat_del = await async_client.post(
        f"/api/v1/zones/{zb_id}/seats", json={"serial_number": 2, "row": 1, "column": 2}
    )
    assert post_seat_del.status_code == 404, "Creating seat under deleted zone must return 404"

    # Attempt batch creating seats under soft-deleted zone
    post_batch_del = await async_client.post(
        f"/api/v1/zones/{zb_id}/seats/batch",
        json={"seats": [{"serial_number": 3, "row": 1, "column": 3}]},
    )
    assert post_batch_del.status_code == 404, "Batch creating seats under deleted zone must return 404"


@pytest.mark.asyncio
async def test_partially_soft_deleted_hierarchy_filtering(container: Container, async_client: AsyncClient):
    """
    Verifies that if a specific Zone or Seat in a venue is individually soft-deleted
    (e.g., via DB administration or prior operations), list queries correctly filter out
    the soft-deleted records while returning active sibling records.
    """
    db = container.db()
    async with db.get_async_session() as session:
        venue = Venue(name="Partial Active Venue", address="300 Active Rd")
        session.add(venue)
        await session.flush()
        v_id = venue.id

        z_active = Zone(code="ZONE_ACT", venue_id=v_id, deleted_at=None)
        z_deleted = Zone(code="ZONE_DEL", venue_id=v_id, deleted_at=datetime.now(timezone.utc).replace(tzinfo=None))
        session.add_all([z_active, z_deleted])
        await session.flush()
        za_id = z_active.id
        zd_id = z_deleted.id

        s_active = Seat(serial_number=1, row=1, column=1, zone_id=za_id, deleted_at=None)
        s_deleted = Seat(serial_number=2, row=1, column=2, zone_id=za_id, deleted_at=datetime.now(timezone.utc).replace(tzinfo=None))
        session.add_all([s_active, s_deleted])
        await session.commit()

    # List zones under venue -> only active zone returned
    z_resp = await async_client.get(f"/api/v1/venues/{v_id}/zones")
    assert z_resp.status_code == 200
    zones = z_resp.json()
    assert len(zones) == 1
    assert zones[0]["id"] == za_id

    # List seats under active zone -> only active seat returned
    s_resp = await async_client.get(f"/api/v1/zones/{za_id}/seats")
    assert s_resp.status_code == 200
    seats = s_resp.json()
    assert len(seats) == 1
    assert seats[0]["id"] == s_active.id


# ============================================================================
# Additional Adversarial Tests: Rollback Atomicity, Concurrency & Edge Cases
# ============================================================================

@pytest.mark.asyncio
async def test_cascade_delete_empty_venue_and_empty_zones(async_client: AsyncClient, container: Container):
    """
    Edge case: Verifies that cascading delete operates smoothly without errors
    when deleting:
    1. A venue with 0 zones.
    2. A venue with zones that have 0 seats.
    """
    # 1. Venue with 0 zones
    v1_resp = await async_client.post("/api/v1/venues", json={"name": "Empty Venue", "address": "Nowhere"})
    assert v1_resp.status_code == 201
    v1_id = v1_resp.json()["id"]

    del1 = await async_client.delete(f"/api/v1/venues/{v1_id}")
    assert del1.status_code == 200

    # 2. Venue with 2 zones and 0 seats
    v2_resp = await async_client.post("/api/v1/venues", json={"name": "Empty Zones Venue", "address": "Somewhere"})
    assert v2_resp.status_code == 201
    v2_id = v2_resp.json()["id"]

    z1_resp = await async_client.post(f"/api/v1/venues/{v2_id}/zones", json={"code": "EZ1"})
    z2_resp = await async_client.post(f"/api/v1/venues/{v2_id}/zones", json={"code": "EZ2"})
    assert z1_resp.status_code == 201
    assert z2_resp.status_code == 201
    z1_id = z1_resp.json()["id"]
    z2_id = z2_resp.json()["id"]

    del2 = await async_client.delete(f"/api/v1/venues/{v2_id}")
    assert del2.status_code == 200

    db = container.db()
    async with db.get_async_session() as session:
        v2_db = (await session.execute(select(Venue).where(Venue.id == v2_id))).scalar_one()
        assert v2_db.deleted_at is not None

        z_dbs = (await session.execute(select(Zone).where(Zone.venue_id == v2_id))).scalars().all()
        assert len(z_dbs) == 2
        assert all(z.deleted_at is not None for z in z_dbs)


@pytest.mark.asyncio
async def test_cascade_delete_simulated_fault_transaction_rollback(container: Container, monkeypatch):
    """
    Verifies that if an unexpected exception occurs before committing the cascading soft delete,
    the entire transaction is rolled back and NO entity is marked as deleted.
    """
    venue_repo = container.venue_repository()
    venue_data = VenueCreate(name="Fault Test Venue", address="Fault St")
    venue = await venue_repo.create_venue(venue_data)
    zone = await venue_repo.create_zone(venue.id, ZoneCreate(code="FAULT_ZONE"))
    seat = await venue_repo.create_seat(zone.id, SeatCreate(serial_number=1, row=1, column=1))

    # Monkeypatch to inject a runtime fault during commit
    original_session_factory = venue_repo.session_factory

    class FaultySessionContext:
        def __init__(self, real_ctx):
            self.real_ctx = real_ctx
            self.session = None

        async def __aenter__(self):
            self.session = await self.real_ctx.__aenter__()
            # Monkeypatch commit on this session instance to raise RuntimeError
            async def faulty_commit():
                raise RuntimeError("Simulated Database / Disk Write Failure")
            self.session.commit = faulty_commit
            return self.session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return await self.real_ctx.__aexit__(exc_type, exc_val, exc_tb)

    def faulty_session_factory():
        return FaultySessionContext(original_session_factory())

    monkeypatch.setattr(venue_repo, "session_factory", faulty_session_factory)

    # Trigger delete and verify exception is raised
    with pytest.raises(RuntimeError, match="Simulated Database / Disk Write Failure"):
        await venue_repo.delete_venue_cascade(venue.id)

    # Restore original session factory
    monkeypatch.setattr(venue_repo, "session_factory", original_session_factory)

    # Verify complete rollback in DB: Venue, Zone, Seat all have deleted_at IS NULL
    db = container.db()
    async with db.get_async_session() as session:
        v_check = (await session.execute(select(Venue).where(Venue.id == venue.id))).scalar_one()
        assert v_check.deleted_at is None, "Venue must not be deleted after rollback"

        z_check = (await session.execute(select(Zone).where(Zone.id == zone.id))).scalar_one()
        assert z_check.deleted_at is None, "Zone must not be deleted after rollback"

        s_check = (await session.execute(select(Seat).where(Seat.id == seat.id))).scalar_one()
        assert s_check.deleted_at is None, "Seat must not be deleted after rollback"


@pytest.mark.asyncio
async def test_concurrent_venue_deletion(async_client: AsyncClient, container: Container):
    """
    Verifies that when multiple concurrent requests attempt to delete the same venue:
    - Exactly one request succeeds with 200 OK.
    - Subsequent/concurrent conflicting requests return 404 Not Found.
    - No unhandled race conditions or duplicate deletions corrupt state.
    """
    import asyncio

    v_resp = await async_client.post(
        "/api/v1/venues", json={"name": "Concurrent Target", "address": "Race St"}
    )
    assert v_resp.status_code == 201
    venue_id = v_resp.json()["id"]

    # Add zone and seats
    z_resp = await async_client.post(f"/api/v1/venues/{venue_id}/zones", json={"code": "RACE_ZONE"})
    assert z_resp.status_code == 201
    zone_id = z_resp.json()["id"]

    await async_client.post(
        f"/api/v1/zones/{zone_id}/seats/batch",
        json={"seats": [{"serial_number": i, "row": 1, "column": i} for i in range(1, 11)]},
    )

    # Fire 5 concurrent delete requests
    tasks = [async_client.delete(f"/api/v1/venues/{venue_id}") for _ in range(5)]
    responses = await asyncio.gather(*tasks)

    status_codes = [r.status_code for r in responses]
    assert status_codes.count(200) == 1, f"Expected exactly one 200 OK, got {status_codes}"
    assert status_codes.count(404) == 4, f"Expected four 404 Not Found, got {status_codes}"

