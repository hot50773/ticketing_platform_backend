"""Venue / Zone / Seat CRD coverage.

The event and booking suites were removed with the old flows: they are to be
rewritten TDD-style once the acceptance criteria are agreed.
"""
import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.containers import Container
from app.models import Seat, Venue, Zone


@pytest.mark.asyncio
async def test_venue_zone_seat_crd_flow(async_client: AsyncClient):
    """Creating a venue, then a zone under it, then a seat under the zone."""
    venue_resp = await async_client.post(
        "/api/v1/venues",
        json={
            "name": "Kaohsiung National Stadium",
            "description": "Main Southern Venue",
            "address": "Kaohsiung City",
        },
    )
    assert venue_resp.status_code == 201
    venue_id = venue_resp.json()["id"]

    get_resp = await async_client.get(f"/api/v1/venues/{venue_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Kaohsiung National Stadium"

    zone_resp = await async_client.post(
        f"/api/v1/venues/{venue_id}/zones", json={"name": "ROCK_VIP"}
    )
    assert zone_resp.status_code == 201
    zone_id = zone_resp.json()["id"]

    seat_resp = await async_client.post(
        f"/api/v1/zones/{zone_id}/seats", json={"row": 1, "col": 1}
    )
    assert seat_resp.status_code == 201
    assert seat_resp.json()["zone_id"] == zone_id
    # serial_number is assigned by the backend, starting at 1 per zone
    assert seat_resp.json()["serial_number"] == 1

    second = await async_client.post(
        f"/api/v1/zones/{zone_id}/seats", json={"row": 1, "col": 2}
    )
    assert second.json()["serial_number"] == 2


@pytest.mark.asyncio
async def test_venue_cascade_soft_delete(
    async_client: AsyncClient, container: Container, seeded_venue_and_seats: dict
):
    """Deleting a venue soft-deletes its zones and seats too."""
    venue_id = seeded_venue_and_seats["venue_id"]

    delete_resp = await async_client.delete(f"/api/v1/venues/{venue_id}")
    assert delete_resp.status_code == 200

    db = container.db()
    async with db.get_async_session() as session:
        venue = (
            await session.execute(select(Venue).where(Venue.id == venue_id))
        ).scalars().one()
        zones = (
            await session.execute(select(Zone).where(Zone.venue_id == venue_id))
        ).scalars().all()
        seats = (
            await session.execute(select(Seat).join(Zone).where(Zone.venue_id == venue_id))
        ).scalars().all()

    assert venue.deleted_at is not None
    assert all(z.deleted_at is not None for z in zones)
    assert all(s.deleted_at is not None for s in seats)


@pytest.mark.asyncio
async def test_create_seats_batch(async_client: AsyncClient, seeded_venue_and_seats: dict):
    """Batch seat creation returns every created seat."""
    zone_id = seeded_venue_and_seats["zone_a_id"]

    resp = await async_client.post(
        f"/api/v1/zones/{zone_id}/seats/batch",
        json={"seats": [{"row": 2, "col": 1}, {"row": 2, "col": 2}]},
    )
    assert resp.status_code == 201
    created = resp.json()
    # zone_a already holds seats 1 and 2, so the batch continues from 3
    assert [s["serial_number"] for s in created] == [3, 4]
    assert all(s["zone_id"] == zone_id for s in created)


@pytest.mark.asyncio
async def test_client_supplied_serial_number_is_ignored(
    async_client: AsyncClient, seeded_venue_and_seats: dict
):
    """serial_number is backend-owned: a client-sent value must not take effect."""
    zone_id = seeded_venue_and_seats["zone_a_id"]
    resp = await async_client.post(
        f"/api/v1/zones/{zone_id}/seats",
        json={"row": 9, "col": 9, "serial_number": 999},
    )
    assert resp.status_code == 201
    assert resp.json()["serial_number"] == 3


@pytest.mark.asyncio
async def test_create_seats_batch_rejects_empty_payload(
    async_client: AsyncClient, seeded_venue_and_seats: dict
):
    """An empty seat list is a validation error, not a silent no-op."""
    zone_id = seeded_venue_and_seats["zone_a_id"]
    resp = await async_client.post(f"/api/v1/zones/{zone_id}/seats/batch", json={"seats": []})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_soft_deleted_name_can_be_reused(async_client: AsyncClient):
    """Uniqueness is enforced by partial indexes, so a soft-deleted venue stops
    occupying its name - but a live duplicate is still rejected."""
    payload = {"name": "Arena", "description": "d", "address": "a"}

    first = await async_client.post("/api/v1/venues", json=payload)
    assert first.status_code == 201

    duplicate = await async_client.post("/api/v1/venues", json=payload)
    assert duplicate.status_code == 409

    assert (await async_client.delete(f"/api/v1/venues/{first.json()['id']}")).status_code == 200

    reused = await async_client.post("/api/v1/venues", json=payload)
    assert reused.status_code == 201
    assert reused.json()["id"] != first.json()["id"]


@pytest.mark.asyncio
async def test_venue_requires_description(async_client: AsyncClient):
    """venue.description is NOT NULL, so the schema must reject a missing one
    with 422 rather than letting it reach the DB as an IntegrityError."""
    resp = await async_client.post(
        "/api/v1/venues", json={"name": "No Description", "address": "a"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_venue_list_pagination(async_client: AsyncClient):
    """GET /venues is paginated: items is windowed, total is the full count."""
    for i in range(3):
        resp = await async_client.post(
            "/api/v1/venues",
            json={"name": f"Venue {i}", "description": "d", "address": "a"},
        )
        assert resp.status_code == 201

    first = (await async_client.get("/api/v1/venues?limit=2&offset=0")).json()
    assert first["total"] == 3
    assert first["limit"] == 2 and first["offset"] == 0
    assert [v["name"] for v in first["items"]] == ["Venue 0", "Venue 1"]

    second = (await async_client.get("/api/v1/venues?limit=2&offset=2")).json()
    assert second["total"] == 3
    assert [v["name"] for v in second["items"]] == ["Venue 2"]

    default = (await async_client.get("/api/v1/venues")).json()
    assert default["limit"] == 20 and default["offset"] == 0

    assert (await async_client.get("/api/v1/venues?limit=0")).status_code == 422
    assert (await async_client.get("/api/v1/venues?limit=201")).status_code == 422


@pytest.mark.asyncio
async def test_zone_and_seat_list_pagination(
    async_client: AsyncClient, seeded_venue_and_seats: dict
):
    """Zone and seat lists are paginated the same way."""
    venue_id = seeded_venue_and_seats["venue_id"]
    zone_id = seeded_venue_and_seats["zone_a_id"]

    zones = (await async_client.get(f"/api/v1/venues/{venue_id}/zones?limit=1")).json()
    assert zones["total"] == 2 and len(zones["items"]) == 1

    seats = (await async_client.get(f"/api/v1/zones/{zone_id}/seats?limit=1&offset=1")).json()
    assert seats["total"] == 2 and len(seats["items"]) == 1
    assert seats["items"][0]["serial_number"] == 2
