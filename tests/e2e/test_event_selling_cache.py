"""Selling list cache against real Postgres and Redis.

ASGITransport returns only after the response's background tasks have run, so
the refresh a read triggers is done by the time the next request goes out.
"""
import asyncio
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import event as sa_event
from sqlmodel import text

from app.cache.event_list import EventListCache
from app.containers import Container
from app.enums import EventListMode, PageSize
from app.libs.clock.functional import now_utc
from app.models import Event
from app.schemas import PaginationParams
from app.schemas.event import EventListCacheFieldInternal
from app.services.mappers import EventSchemaMapper

API = "/api/v1/events"


async def seed_events(container: Container, venue_id: int, selling: int) -> list[int]:
    """`selling` events on sale, plus one unpublished, one started and one deleted."""
    future = now_utc() + timedelta(days=30)
    past = now_utc() - timedelta(hours=1)

    def an_event(name: str, is_active: bool, start_at, deleted: bool = False) -> Event:
        return Event(
            name=name, description="d", start_at=start_at, end_at=start_at + timedelta(hours=3),
            is_active=is_active, venue_id=venue_id, deleted_at=now_utc() if deleted else None,
        )

    async with container.db().get_async_session() as session:
        on_sale = [an_event(f"selling-{i}", True, future) for i in range(selling)]
        session.add_all(on_sale)
        session.add_all([
            an_event("unpublished", False, future),
            an_event("started", True, past),
            an_event("deleted", True, future, deleted=True),
        ])
        await session.commit()
        return [event.id for event in on_sale]


async def make_cache_stale(container: Container) -> None:
    redis = container.redis_client()
    fields = await redis.hgetall(EventListCache.EVENT_LIST_KEY)
    for field, raw in fields.items():
        entry = EventListCacheFieldInternal.model_validate_json(raw)
        entry.expired_at = now_utc() - timedelta(seconds=1)
        await redis.hset(EventListCache.EVENT_LIST_KEY, field, entry.model_dump_json())


async def selling_ids(async_client: AsyncClient) -> list[int]:
    response = await async_client.get(f"{API}/selling?page=1&page_size=50")
    assert response.status_code == 200
    return [item["id"] for item in response.json()["items"]]


# --------------------------------------------------------------------------- #
# Rebuild
# --------------------------------------------------------------------------- #
async def test_rebuild_matches_the_paged_db_query_for_every_cached_page(
    container: Container, seeded_venue_and_seats: dict
):
    await seed_events(container, seeded_venue_and_seats["venue_id"], selling=23)

    await container.event_service()._rebuild_selling_events_cache()

    cache = container.event_list_cache()
    repository = container.event_repository()
    mapper = EventSchemaMapper()
    for size in PageSize:
        for page in range(1, 6):
            pagination = PaginationParams(page=page, page_size=size)
            cached = await cache.get_paged_events(pagination)
            expected = mapper.to_event_page(
                await repository.get_events(pagination, EventListMode.SELLING)
            )
            assert cached is not None
            assert cached.data == expected


async def test_rebuild_leaves_out_unpublished_started_and_deleted_events(
    container: Container, seeded_venue_and_seats: dict
):
    on_sale = await seed_events(container, seeded_venue_and_seats["venue_id"], selling=2)

    await container.event_service()._rebuild_selling_events_cache()

    cached = await container.event_list_cache().get_paged_events(PaginationParams())
    assert [item.id for item in cached.data.items] == on_sale
    assert cached.data.total == 2


async def test_rebuild_runs_two_sql_statements(
    container: Container, seeded_venue_and_seats: dict
):
    await seed_events(container, seeded_venue_and_seats["venue_id"], selling=23)
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    engine = container.db()._engine.sync_engine
    sa_event.listen(engine, "before_cursor_execute", record)
    try:
        await container.event_service()._rebuild_selling_events_cache()
    finally:
        sa_event.remove(engine, "before_cursor_execute", record)

    assert len(statements) == 2


async def test_rebuild_beyond_five_pages_keeps_the_full_total(
    container: Container, seeded_venue_and_seats: dict
):
    on_sale = await seed_events(container, seeded_venue_and_seats["venue_id"], selling=260)

    await container.event_service()._rebuild_selling_events_cache()

    cache = container.event_list_cache()
    last = await cache.get_paged_events(PaginationParams(page=5, page_size=PageSize.LARGE))
    assert [item.id for item in last.data.items] == on_sale[200:250]
    assert last.data.total == 260
    assert await cache.get_paged_events(PaginationParams(page=6, page_size=PageSize.LARGE)) is None


# --------------------------------------------------------------------------- #
# Cache storage
# --------------------------------------------------------------------------- #
async def test_refresh_sets_a_ten_minute_ttl_and_a_five_minute_soft_expiry(
    container: Container, seeded_venue_and_seats: dict
):
    await seed_events(container, seeded_venue_and_seats["venue_id"], selling=1)
    before = now_utc()

    await container.event_service()._rebuild_selling_events_cache()

    ttl = await container.redis_client().ttl(EventListCache.EVENT_LIST_KEY)
    assert 590 < ttl <= 600
    cached = await container.event_list_cache().get_paged_events(PaginationParams())
    assert before + timedelta(seconds=299) < cached.expired_at <= now_utc() + timedelta(seconds=300)


async def test_concurrent_rebuilds_do_not_trample_each_other(
    container: Container, seeded_venue_and_seats: dict
):
    """Both used to stage into one temp key; the second RENAME found it gone."""
    await seed_events(container, seeded_venue_and_seats["venue_id"], selling=23)
    service = container.event_service()

    await asyncio.gather(*(service._rebuild_selling_events_cache() for _ in range(5)))

    fields = await container.redis_client().hkeys(EventListCache.EVENT_LIST_KEY)
    assert len(fields) == 10


async def test_refresh_lock_admits_one_holder_and_expires_on_its_own(container: Container):
    cache = container.event_list_cache()

    assert await cache.acquire_refresh_lock() is True
    assert await cache.acquire_refresh_lock() is False
    assert 0 < await container.redis_client().ttl(EventListCache.REFRESH_LOCK_KEY) <= 10

    await cache.release_refresh_lock()
    assert await cache.acquire_refresh_lock() is True


# --------------------------------------------------------------------------- #
# Read path
# --------------------------------------------------------------------------- #
async def test_started_event_drops_off_after_the_soft_expiry(
    async_client: AsyncClient, container: Container, seeded_venue_and_seats: dict
):
    first, second = await seed_events(container, seeded_venue_and_seats["venue_id"], selling=2)
    await container.event_service()._rebuild_selling_events_cache()
    async with container.db().get_async_session() as session:
        await session.execute(
            text("UPDATE event SET start_at = now() - interval '1 minute' WHERE id = :id"),
            {"id": first},
        )
        await session.commit()
    await make_cache_stale(container)

    assert await selling_ids(async_client) == [first, second]
    assert await selling_ids(async_client) == [second]


async def test_profile_change_shows_up_after_the_soft_expiry(
    async_client: AsyncClient, container: Container, seeded_venue_and_seats: dict
):
    (event_id,) = await seed_events(container, seeded_venue_and_seats["venue_id"], selling=1)
    await container.event_service()._rebuild_selling_events_cache()
    response = await async_client.patch(f"{API}/{event_id}/profile", json={"name": "Renamed", "description": "d"})
    assert response.status_code == 200
    await make_cache_stale(container)

    await selling_ids(async_client)

    response = await async_client.get(f"{API}/selling")
    assert response.json()["items"][0]["name"] == "Renamed"


async def test_expired_key_falls_back_to_the_db_and_is_rebuilt(
    async_client: AsyncClient, container: Container, seeded_venue_and_seats: dict
):
    on_sale = await seed_events(container, seeded_venue_and_seats["venue_id"], selling=2)

    assert await selling_ids(async_client) == on_sale

    assert await container.redis_client().exists(EventListCache.EVENT_LIST_KEY) == 1
