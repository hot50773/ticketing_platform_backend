import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.containers import Container
from app.enums import SeatStatus
from app.libs.clock.functional import now_utc
from app.models import EventSeat

API = "/api/v1/events"
ADMIN_API = "/api/v1/admin/events"


async def publish(client: AsyncClient, seeded_event: dict):
    event_id = seeded_event["event_id"]
    for name, zone_id in seeded_event["zone_ids"].items():
        await client.patch(
            f"{ADMIN_API}/{event_id}/event_zones/{zone_id}/profile",
            json={"name": name, "price": 1500},
        )
    resp = await client.post(f"{ADMIN_API}/{event_id}/publish")
    assert resp.status_code == 200, resp.text


async def read_seats(container: Container, zone_id: int) -> list[EventSeat]:
    db = container.db()
    async with db.get_async_session() as session:
        return list(
            (
                await session.execute(
                    select(EventSeat)
                    .where(EventSeat.event_zone_id == zone_id)
                    .where(EventSeat.deleted_at == None)
                    .order_by(EventSeat.serial_number)
                )
            ).scalars().all()
        )


# --------------------------------------------------------------------------- #
# create_event_zone
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_create_zone_numbers_seats_from_one(
    async_client: AsyncClient, seeded_event: dict
):
    resp = await async_client.post(
        f"{ADMIN_API}/{seeded_event['event_id']}/event_zones",
        json={
            "name": "VIP",
            "price": 9000,
            "seats": [{"row": 1, "col": 1}, {"row": 1, "col": 2}, {"row": 2, "col": 1}],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "VIP"
    assert body["price"] == 9000
    assert body["seat_count"] == 3


@pytest.mark.asyncio
async def test_create_zone_without_seats(async_client: AsyncClient, seeded_event: dict):
    resp = await async_client.post(
        f"{ADMIN_API}/{seeded_event['event_id']}/event_zones", json={"name": "Empty"}
    )
    assert resp.status_code == 201
    assert resp.json()["seat_count"] == 0
    assert resp.json()["price"] is None


@pytest.mark.asyncio
async def test_create_zone_rejects_duplicate_name(
    async_client: AsyncClient, seeded_event: dict
):
    resp = await async_client.post(
        f"{ADMIN_API}/{seeded_event['event_id']}/event_zones", json={"name": "ZONE_A"}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_zone_rejects_duplicate_positions(
    async_client: AsyncClient, seeded_event: dict
):
    resp = await async_client.post(
        f"{ADMIN_API}/{seeded_event['event_id']}/event_zones",
        json={"name": "Dupes", "seats": [{"row": 1, "col": 1}, {"row": 1, "col": 1}]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_zone_rejected_after_publishing(
    async_client: AsyncClient, seeded_event: dict
):
    await publish(async_client, seeded_event)
    resp = await async_client.post(
        f"{ADMIN_API}/{seeded_event['event_id']}/event_zones", json={"name": "Late"}
    )
    assert resp.status_code == 409
    assert "unpublished" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# update_event_zone_profile
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_zone_profile(async_client: AsyncClient, seeded_event: dict):
    zone_id = seeded_event["zone_ids"]["ZONE_A"]
    resp = await async_client.patch(
        f"{ADMIN_API}/{seeded_event['event_id']}/event_zones/{zone_id}/profile",
        json={"name": "Floor", "price": 3200},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Floor"
    assert resp.json()["price"] == 3200
    assert resp.json()["seat_count"] == 2


@pytest.mark.asyncio
async def test_zone_profile_still_editable_after_publishing(
    async_client: AsyncClient, seeded_event: dict
):
    """Per the corrected spec: name and price stay editable once live."""
    await publish(async_client, seeded_event)
    zone_id = seeded_event["zone_ids"]["ZONE_A"]
    resp = await async_client.patch(
        f"{ADMIN_API}/{seeded_event['event_id']}/event_zones/{zone_id}/profile",
        json={"name": "Floor Live", "price": 4200},
    )
    assert resp.status_code == 200
    assert resp.json()["price"] == 4200


@pytest.mark.asyncio
async def test_zone_of_another_event_is_404(async_client: AsyncClient, seeded_event: dict):
    zone_id = seeded_event["zone_ids"]["ZONE_A"]
    resp = await async_client.patch(
        f"{ADMIN_API}/9999/event_zones/{zone_id}/profile", json={"name": "x", "price": 1}
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# replace_event_zone_seats
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_layout_sync_adds_and_removes_without_renumbering(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    """The core rule: survivors keep their serial_number, and a retired number
    is never recycled - the new seat gets max + 1, not the freed 1."""
    event_id = seeded_event["event_id"]
    zone_id = seeded_event["zone_ids"]["ZONE_A"]

    before = await read_seats(container, zone_id)
    assert [(s.serial_number, s.row, s.col) for s in before] == [(0, 1, 1), (1, 1, 2)]

    resp = await async_client.patch(
        f"{ADMIN_API}/{event_id}/event_zones/{zone_id}/seats",
        json={"seats": [{"row": 1, "col": 1}, {"row": 1, "col": 3}]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"created_count": 1, "deleted_count": 1, "seat_count": 2}

    after = await read_seats(container, zone_id)
    assert [(s.serial_number, s.row, s.col) for s in after] == [(0, 1, 1), (2, 1, 3)]


@pytest.mark.asyncio
async def test_layout_sync_is_idempotent(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    """Sending the current layout back must change nothing."""
    event_id = seeded_event["event_id"]
    zone_id = seeded_event["zone_ids"]["ZONE_A"]

    resp = await async_client.patch(
        f"{ADMIN_API}/{event_id}/event_zones/{zone_id}/seats",
        json={"seats": [{"row": 1, "col": 1}, {"row": 1, "col": 2}]},
    )
    assert resp.json() == {"created_count": 0, "deleted_count": 0, "seat_count": 2}

    after = await read_seats(container, zone_id)
    assert [s.serial_number for s in after] == [0, 1]


@pytest.mark.asyncio
async def test_layout_sync_can_empty_a_zone(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    zone_id = seeded_event["zone_ids"]["ZONE_A"]

    resp = await async_client.patch(
        f"{ADMIN_API}/{event_id}/event_zones/{zone_id}/seats", json={"seats": []}
    )
    assert resp.json() == {"created_count": 0, "deleted_count": 2, "seat_count": 0}
    assert await read_seats(container, zone_id) == []


@pytest.mark.asyncio
async def test_layout_sync_rejects_duplicate_positions(
    async_client: AsyncClient, seeded_event: dict
):
    zone_id = seeded_event["zone_ids"]["ZONE_A"]
    resp = await async_client.patch(
        f"{ADMIN_API}/{seeded_event['event_id']}/event_zones/{zone_id}/seats",
        json={"seats": [{"row": 5, "col": 5}, {"row": 5, "col": 5}]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_layout_sync_rejected_after_publishing(
    async_client: AsyncClient, seeded_event: dict
):
    await publish(async_client, seeded_event)
    zone_id = seeded_event["zone_ids"]["ZONE_A"]
    resp = await async_client.patch(
        f"{ADMIN_API}/{seeded_event['event_id']}/event_zones/{zone_id}/seats",
        json={"seats": [{"row": 1, "col": 1}]},
    )
    assert resp.status_code == 409
    assert "unpublished" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_layout_sync_rejected_when_a_seat_is_held(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    """Even while unpublished, a RESERVED seat must not be dropped."""
    zone_id = seeded_event["zone_ids"]["ZONE_A"]
    db = container.db()
    async with db.get_async_session() as session:
        seat = (
            await session.execute(
                select(EventSeat).where(EventSeat.event_zone_id == zone_id).limit(1)
            )
        ).scalars().one()
        seat.status = SeatStatus.RESERVED
        session.add(seat)
        await session.commit()

    resp = await async_client.patch(
        f"{ADMIN_API}/{seeded_event['event_id']}/event_zones/{zone_id}/seats",
        json={"seats": [{"row": 9, "col": 9}]},
    )
    assert resp.status_code == 409
    assert "locked or reserved" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_deleted_seat_position_can_be_reused_with_a_new_number(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    """Re-adding a removed position gives a fresh serial_number, never the old one."""
    event_id = seeded_event["event_id"]
    zone_id = seeded_event["zone_ids"]["ZONE_A"]

    await async_client.patch(
        f"{ADMIN_API}/{event_id}/event_zones/{zone_id}/seats",
        json={"seats": [{"row": 1, "col": 1}]},
    )
    await async_client.patch(
        f"{ADMIN_API}/{event_id}/event_zones/{zone_id}/seats",
        json={"seats": [{"row": 1, "col": 1}, {"row": 1, "col": 2}]},
    )

    after = await read_seats(container, zone_id)
    assert [(s.serial_number, s.row, s.col) for s in after] == [(0, 1, 1), (2, 1, 2)]
