"""Reading, updating, publishing and deleting an event."""
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.containers import Container
from app.enums import OrderStatus
from app.libs.clock.functional import now_utc
from app.models import Event, EventSeat, EventZone

API = "/api/v1/events"


async def set_price(client: AsyncClient, event_id: int, zone_id: int, name: str, price: int):
    resp = await client.patch(
        f"{API}/{event_id}/event_zones/{zone_id}/profile",
        json={"name": name, "price": price},
    )
    assert resp.status_code == 200, resp.text
    return resp


async def price_every_zone(client: AsyncClient, seeded_event: dict):
    for name, zone_id in seeded_event["zone_ids"].items():
        await set_price(client, seeded_event["event_id"], zone_id, name, 1500)


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_events_is_paginated(async_client: AsyncClient, seeded_event: dict):
    body = (await async_client.get(f"{API}?limit=1")).json()
    assert body["total"] == 1
    assert body["limit"] == 1
    assert body["items"][0]["id"] == seeded_event["event_id"]


@pytest.mark.asyncio
async def test_get_event_returns_zones_with_seat_counts(
    async_client: AsyncClient, seeded_event: dict
):
    resp = await async_client.get(f"{API}/{seeded_event['event_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert {z["name"]: z["seat_count"] for z in body["zones"]} == {"ZONE_A": 2, "ZONE_B": 2}


@pytest.mark.asyncio
async def test_get_unknown_event_is_404(async_client: AsyncClient):
    assert (await async_client.get(f"{API}/9999")).status_code == 404


@pytest.mark.asyncio
async def test_deleted_event_disappears_from_list_and_get(
    async_client: AsyncClient, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    assert (await async_client.delete(f"{API}/{event_id}")).status_code == 204

    assert (await async_client.get(f"{API}")).json()["total"] == 0
    assert (await async_client.get(f"{API}/{event_id}")).status_code == 404


@pytest.mark.asyncio
async def test_list_zone_seats_is_paginated(async_client: AsyncClient, seeded_event: dict):
    event_id = seeded_event["event_id"]
    zone_id = seeded_event["zone_ids"]["ZONE_A"]

    body = (await async_client.get(f"{API}/{event_id}/event_zones/{zone_id}/seats")).json()
    assert body["total"] == 2
    assert [s["serial_number"] for s in body["items"]] == [1, 2]
    assert all(s["status"] == "AVAILABLE" for s in body["items"])


@pytest.mark.asyncio
async def test_zone_seats_of_wrong_event_is_404(async_client: AsyncClient, seeded_event: dict):
    """A zone id that belongs to another event must not leak through."""
    zone_id = seeded_event["zone_ids"]["ZONE_A"]
    assert (await async_client.get(f"{API}/9999/event_zones/{zone_id}/seats")).status_code == 404


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_profile(async_client: AsyncClient, seeded_event: dict):
    resp = await async_client.patch(
        f"{API}/{seeded_event['event_id']}/profile",
        json={"name": "Renamed", "description": "New blurb"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"
    assert resp.json()["description"] == "New blurb"


@pytest.mark.asyncio
async def test_update_profile_refreshes_updated_at(
    async_client: AsyncClient, seeded_event: dict
):
    """updated_at must actually move; server_onupdate alone emitted no SQL."""
    before = (await async_client.get(f"{API}/{seeded_event['event_id']}")).json()["updated_at"]
    resp = await async_client.patch(
        f"{API}/{seeded_event['event_id']}/profile",
        json={"name": "Renamed", "description": "d"},
    )
    assert resp.json()["updated_at"] > before


@pytest.mark.asyncio
async def test_update_profile_allowed_after_publishing(
    async_client: AsyncClient, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    await price_every_zone(async_client, seeded_event)
    assert (await async_client.post(f"{API}/{event_id}/publish")).status_code == 200

    resp = await async_client.patch(
        f"{API}/{event_id}/profile", json={"name": "Renamed Live", "description": "d"}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_update_profile_unknown_event_is_404(async_client: AsyncClient):
    resp = await async_client.patch(f"{API}/9999/profile", json={"name": "x", "description": "d"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Holding time
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_holding_time(async_client: AsyncClient, seeded_event: dict):
    start_at = now_utc() + timedelta(days=60)
    resp = await async_client.patch(
        f"{API}/{seeded_event['event_id']}/holding_time",
        json={"start_at": start_at.isoformat(), "end_at": (start_at + timedelta(hours=2)).isoformat()},
    )
    assert resp.status_code == 200
    assert resp.json()["start_at"].startswith(start_at.isoformat()[:16])


@pytest.mark.asyncio
async def test_update_holding_time_rejected_after_publishing(
    async_client: AsyncClient, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    await price_every_zone(async_client, seeded_event)
    await async_client.post(f"{API}/{event_id}/publish")

    start_at = now_utc() + timedelta(days=60)
    resp = await async_client.patch(
        f"{API}/{event_id}/holding_time",
        json={"start_at": start_at.isoformat(), "end_at": (start_at + timedelta(hours=2)).isoformat()},
    )
    assert resp.status_code == 409
    assert "unpublished" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_holding_time_rejects_past_start(
    async_client: AsyncClient, seeded_event: dict
):
    past = now_utc() - timedelta(days=1)
    resp = await async_client.patch(
        f"{API}/{seeded_event['event_id']}/holding_time",
        json={"start_at": past.isoformat(), "end_at": (past + timedelta(hours=2)).isoformat()},
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Publish / unpublish
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_publish_requires_every_zone_priced(
    async_client: AsyncClient, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    await set_price(async_client, event_id, seeded_event["zone_ids"]["ZONE_A"], "ZONE_A", 1500)

    resp = await async_client.post(f"{API}/{event_id}/publish")
    assert resp.status_code == 409
    assert "ZONE_B" in resp.json()["detail"]
    assert "ZONE_A" not in resp.json()["detail"]


@pytest.mark.asyncio
async def test_publish_succeeds_once_every_zone_is_priced(
    async_client: AsyncClient, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    await price_every_zone(async_client, seeded_event)

    resp = await async_client.post(f"{API}/{event_id}/publish")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


@pytest.mark.asyncio
async def test_publish_rejected_without_any_zone(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    """An event whose zones were all removed must not be publishable."""
    event_id = seeded_event["event_id"]
    db = container.db()
    async with db.get_async_session() as session:
        for zone in (await session.execute(select(EventZone))).scalars().all():
            zone.deleted_at = now_utc()
            session.add(zone)
        await session.commit()

    resp = await async_client.post(f"{API}/{event_id}/publish")
    assert resp.status_code == 409
    assert "no zone" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_unpublish_blocked_by_an_order(
    async_client: AsyncClient, seeded_event: dict, place_order
):
    event_id = seeded_event["event_id"]
    await price_every_zone(async_client, seeded_event)
    await async_client.post(f"{API}/{event_id}/publish")

    await place_order(event_id, seeded_event["user_id"])

    resp = await async_client.post(f"{API}/{event_id}/unpublish")
    assert resp.status_code == 409
    assert "order" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cancelled_order_still_blocks(
    async_client: AsyncClient, seeded_event: dict, place_order
):
    """Per the agreed rule: CANCELLED counts, only soft-deleted stops counting."""
    event_id = seeded_event["event_id"]
    await place_order(event_id, seeded_event["user_id"], status=OrderStatus.CANCELLED)

    resp = await async_client.post(f"{API}/{event_id}/unpublish")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_soft_deleted_order_does_not_block(
    async_client: AsyncClient, seeded_event: dict, place_order
):
    """A system-released expired cart is soft-deleted and must not block."""
    event_id = seeded_event["event_id"]
    await place_order(event_id, seeded_event["user_id"], soft_deleted=True)

    resp = await async_client.post(f"{API}/{event_id}/unpublish")
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_delete_cascades_to_zones_and_seats(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    assert (await async_client.delete(f"{API}/{event_id}")).status_code == 204

    db = container.db()
    async with db.get_async_session() as session:
        event = (await session.execute(select(Event))).scalars().one()
        zones = (await session.execute(select(EventZone))).scalars().all()
        seats = (await session.execute(select(EventSeat))).scalars().all()

    assert event.deleted_at is not None
    assert zones and all(z.deleted_at is not None for z in zones)
    assert seats and all(s.deleted_at is not None for s in seats)


@pytest.mark.asyncio
async def test_delete_blocked_by_any_order(
    async_client: AsyncClient, seeded_event: dict, place_order
):
    event_id = seeded_event["event_id"]
    await place_order(event_id, seeded_event["user_id"])

    resp = await async_client.delete(f"{API}/{event_id}")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_delete_unknown_event_is_404(async_client: AsyncClient):
    assert (await async_client.delete(f"{API}/9999")).status_code == 404
