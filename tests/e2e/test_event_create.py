"""Creating an event clones the venue's layout into EventZone / EventSeat."""
import pytest
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient
from sqlmodel import select

from app.containers import Container
from app.enums import SeatStatus
from app.exceptions import EntityNotFoundError
from app.libs.clock.functional import now_utc
from app.models import Event, EventSeat, EventZone, Seat, Venue, Zone
from app.schemas.event import EventCreate

FUTURE_START = now_utc() + timedelta(days=30)
FUTURE_END = FUTURE_START + timedelta(hours=3)


@pytest.fixture
async def venue_template(container: Container) -> dict:
    """A venue whose template deliberately contains soft-deleted rows:

    zone A  : seats 1, 2, 3   (seat 2 soft-deleted)
    zone B  : seats 1, 2
    zone C  : soft-deleted, seats 1, 2
    """
    db = container.db()
    async with db.get_async_session() as session:
        venue = Venue(name="Template Arena", description="d", address="a")
        session.add(venue)
        await session.flush()

        zone_a = Zone(name="A", venue_id=venue.id)
        zone_b = Zone(name="B", venue_id=venue.id)
        zone_c = Zone(name="C", venue_id=venue.id, deleted_at=now_utc())
        session.add_all([zone_a, zone_b, zone_c])
        await session.flush()

        session.add_all([
            Seat(serial_number=0, row=1, col=1, zone_id=zone_a.id),
            Seat(serial_number=1, row=1, col=2, zone_id=zone_a.id, deleted_at=now_utc()),
            Seat(serial_number=2, row=1, col=3, zone_id=zone_a.id),
            Seat(serial_number=0, row=1, col=1, zone_id=zone_b.id),
            Seat(serial_number=1, row=1, col=2, zone_id=zone_b.id),
            Seat(serial_number=0, row=1, col=1, zone_id=zone_c.id),
            Seat(serial_number=1, row=1, col=2, zone_id=zone_c.id),
        ])
        await session.commit()
        return {"venue_id": venue.id, "zone_a_id": zone_a.id, "zone_b_id": zone_b.id}


async def clone(repo, venue_id: int):
    """Runs the create use case and returns the resulting layout."""
    async with repo.create(payload(venue_id)) as event:
        return await event.layout()


def payload(venue_id: int) -> EventCreate:
    return EventCreate(
        name="World Tour 2026",
        description="Mega Live Concert",
        start_at=FUTURE_START,
        end_at=FUTURE_END,
        venue_id=venue_id,
    )


async def read_layout(container: Container, event_id: int):
    db = container.db()
    async with db.get_async_session() as session:
        zones = (
            await session.execute(
                select(EventZone).where(EventZone.event_id == event_id).order_by(EventZone.name)
            )
        ).scalars().all()
        seats = (
            await session.execute(
                select(EventSeat)
                .join(EventZone, EventSeat.event_zone_id == EventZone.id)
                .where(EventZone.event_id == event_id)
                .order_by(EventZone.name, EventSeat.serial_number)
            )
        ).scalars().all()
        return list(zones), list(seats)


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_clone_copies_only_live_zones_and_seats(container: Container, venue_template: dict):
    """A1 / A5 / A6: soft-deleted template rows are not copied."""
    repo = container.event_repository()
    layout = await clone(repo, venue_template["venue_id"])

    zones, seats = await read_layout(container, layout.event.id)
    assert [z.name for z in zones] == ["A", "B"]  # zone C was soft-deleted
    by_zone = {z.id: z.name for z in zones}
    assert sorted((by_zone[s.event_zone_id], s.serial_number) for s in seats) == [
        ("A", 0), ("A", 2), ("B", 0), ("B", 1)
    ]


@pytest.mark.asyncio
async def test_clone_reuses_template_serial_numbers_and_allows_holes(
    container: Container, venue_template: dict
):
    """A3 / A6: serial_number is copied verbatim, so zone A keeps the 0, 2 hole."""
    repo = container.event_repository()
    layout = await clone(repo, venue_template["venue_id"])

    zones, seats = await read_layout(container, layout.event.id)
    zone_a = next(z for z in zones if z.name == "A")
    assert sorted(s.serial_number for s in seats if s.event_zone_id == zone_a.id) == [0, 2]


@pytest.mark.asyncio
async def test_clone_creates_unpublished_event_with_unset_prices(
    container: Container, venue_template: dict
):
    """A2: unpublished, every price NULL, every seat AVAILABLE."""
    repo = container.event_repository()
    layout = await clone(repo, venue_template["venue_id"])

    zones, seats = await read_layout(container, layout.event.id)
    assert layout.event.is_active is False
    assert all(z.price is None for z in zones)
    assert all(s.status == SeatStatus.AVAILABLE for s in seats)


@pytest.mark.asyncio
async def test_clone_records_template_origin(container: Container, venue_template: dict):
    """A4: source_zone_id / source_seat_id point back at the template."""
    repo = container.event_repository()
    layout = await clone(repo, venue_template["venue_id"])

    zones, seats = await read_layout(container, layout.event.id)
    assert {z.name: z.source_zone_id for z in zones} == {
        "A": venue_template["zone_a_id"],
        "B": venue_template["zone_b_id"],
    }
    assert all(s.source_seat_id is not None for s in seats)

    db = container.db()
    async with db.get_async_session() as session:
        template_ids = set(
            (await session.execute(select(Seat.id).where(Seat.deleted_at == None))).scalars().all()
        )
    assert {s.source_seat_id for s in seats} <= template_ids


@pytest.mark.asyncio
async def test_clone_returns_seat_count_per_zone(container: Container, venue_template: dict):
    repo = container.event_repository()
    layout = await clone(repo, venue_template["venue_id"])

    assert {z.zone.name: z.seat_count for z in layout.zones} == {"A": 2, "B": 2}


@pytest.mark.asyncio
async def test_venue_without_zone_still_creates_an_event(container: Container):
    """A7: an empty template is allowed; zones get added manually later."""
    db = container.db()
    async with db.get_async_session() as session:
        venue = Venue(name="Empty Venue", description="d", address="a")
        session.add(venue)
        await session.commit()
        venue_id = venue.id

    repo = container.event_repository()
    layout = await clone(repo, venue_id)

    assert layout.event.id is not None
    assert layout.zones == []


@pytest.mark.asyncio
async def test_clone_rejects_unknown_venue(container: Container):
    """A8."""
    repo = container.event_repository()
    with pytest.raises(EntityNotFoundError):
        await clone(repo, 9999)


@pytest.mark.asyncio
async def test_clone_rejects_soft_deleted_venue(container: Container):
    """A9."""
    db = container.db()
    async with db.get_async_session() as session:
        venue = Venue(name="Gone", description="d", address="a", deleted_at=now_utc())
        session.add(venue)
        await session.commit()
        venue_id = venue.id

    repo = container.event_repository()
    with pytest.raises(EntityNotFoundError):
        await clone(repo, venue_id)


@pytest.mark.asyncio
async def test_clone_is_atomic(container: Container, venue_template: dict, monkeypatch):
    """A10: if seat cloning fails, no event and no zone may survive."""
    repo = container.event_repository()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("seat cloning blew up")

    monkeypatch.setattr(type(repo), "_clone_seats", boom)

    with pytest.raises(RuntimeError):
        await clone(repo, venue_template["venue_id"])

    db = container.db()
    async with db.get_async_session() as session:
        assert (await session.execute(select(Event))).scalars().all() == []
        assert (await session.execute(select(EventZone))).scalars().all() == []
        assert (await session.execute(select(EventSeat))).scalars().all() == []


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_post_event_returns_detail(async_client: AsyncClient, venue_template: dict):
    """C1."""
    resp = await async_client.post(
        "/api/v1/admin/events",
        json={
            "name": "World Tour 2026",
            "description": "Mega Live Concert",
            "start_at": FUTURE_START.isoformat(),
            "end_at": FUTURE_END.isoformat(),
            "venue_id": venue_template["venue_id"],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["is_active"] is False
    assert {z["name"]: z["seat_count"] for z in body["zones"]} == {"A": 2, "B": 2}
    assert all(z["price"] is None for z in body["zones"])


@pytest.mark.asyncio
async def test_post_event_normalises_timezone(
    async_client: AsyncClient, container: Container, venue_template: dict
):
    """C2: a +08:00 payload is stored as the matching UTC instant."""
    start_local = (now_utc() + timedelta(days=30)).astimezone(timezone(timedelta(hours=8)))
    end_local = start_local + timedelta(hours=3)

    resp = await async_client.post(
        "/api/v1/admin/events",
        json={
            "name": "Taipei Night",
            "description": "d",
            "start_at": start_local.isoformat(),
            "end_at": end_local.isoformat(),
            "venue_id": venue_template["venue_id"],
        },
    )
    assert resp.status_code == 201

    db = container.db()
    async with db.get_async_session() as session:
        event = (await session.execute(select(Event))).scalars().one()
    assert event.start_at == start_local
    assert event.start_at.utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_post_event_requires_description(async_client: AsyncClient, venue_template: dict):
    """C3."""
    resp = await async_client.post(
        "/api/v1/admin/events",
        json={
            "name": "No Description",
            "start_at": FUTURE_START.isoformat(),
            "end_at": FUTURE_END.isoformat(),
            "venue_id": venue_template["venue_id"],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_event_rejects_end_before_start(async_client: AsyncClient, venue_template: dict):
    """C4: InvalidEventTimeError maps to 422."""
    resp = await async_client.post(
        "/api/v1/admin/events",
        json={
            "name": "Backwards",
            "description": "d",
            "start_at": FUTURE_END.isoformat(),
            "end_at": FUTURE_START.isoformat(),
            "venue_id": venue_template["venue_id"],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_event_rejects_past_start(async_client: AsyncClient, venue_template: dict):
    """C4: a start in the past is also 422."""
    past = now_utc() - timedelta(days=1)
    resp = await async_client.post(
        "/api/v1/admin/events",
        json={
            "name": "Yesterday",
            "description": "d",
            "start_at": past.isoformat(),
            "end_at": (past + timedelta(hours=3)).isoformat(),
            "venue_id": venue_template["venue_id"],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_event_unknown_venue_is_404(async_client: AsyncClient):
    """C5."""
    resp = await async_client.post(
        "/api/v1/admin/events",
        json={
            "name": "Nowhere",
            "description": "d",
            "start_at": FUTURE_START.isoformat(),
            "end_at": FUTURE_END.isoformat(),
            "venue_id": 9999,
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_event_ignores_client_supplied_zones(
    async_client: AsyncClient, venue_template: dict
):
    """C6: the layout always comes from the template, never from the payload."""
    resp = await async_client.post(
        "/api/v1/admin/events",
        json={
            "name": "Sneaky",
            "description": "d",
            "start_at": FUTURE_START.isoformat(),
            "end_at": FUTURE_END.isoformat(),
            "venue_id": venue_template["venue_id"],
            "zones": [{"name": "VIP", "price": 9999, "seats": [{"row": 1, "col": 1}]}],
        },
    )
    assert resp.status_code == 201
    assert {z["name"] for z in resp.json()["zones"]} == {"A", "B"}
