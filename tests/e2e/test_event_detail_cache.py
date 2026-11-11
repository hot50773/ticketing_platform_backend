"""Event detail cache against real Postgres and Redis.

ASGITransport returns only after the response's background tasks have run, so
whatever a request scheduled is done by the time the next one goes out.
"""
from datetime import timedelta

from httpx import AsyncClient
from sqlmodel import text

from app.cache.event_detail import EventDetailCache
from app.containers import Container
from app.libs.clock.functional import now_utc
from app.services.mappers import EventSchemaMapper

API = "/api/v1/events"
ADMIN_API = "/api/v1/admin/events"


async def cached(container: Container, event_id: int):
    return await container.event_detail_cache().get(event_id)


async def price_every_zone(async_client: AsyncClient, seeded_event: dict, price: int = 1500) -> None:
    for name, zone_id in seeded_event["zone_ids"].items():
        response = await async_client.patch(
            f"{ADMIN_API}/{seeded_event['event_id']}/event_zones/{zone_id}/profile",
            json={"name": name, "price": price},
        )
        assert response.status_code == 200


async def publish(async_client: AsyncClient, seeded_event: dict) -> None:
    await price_every_zone(async_client, seeded_event)
    response = await async_client.post(f"{ADMIN_API}/{seeded_event['event_id']}/publish")
    assert response.status_code == 200


async def make_detail_stale(container: Container, event_id: int) -> None:
    cache = container.event_detail_cache()
    entry = await cache.get(event_id)
    entry.expired_at = now_utc() - timedelta(seconds=1)
    await container.redis_client().set(
        EventDetailCache.KEY.format(event_id=event_id), entry.model_dump_json(), keepttl=True
    )


# --------------------------------------------------------------------------- #
# Read path
# --------------------------------------------------------------------------- #
async def test_first_read_of_a_published_event_fills_the_cache_with_the_db_detail(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    await publish(async_client, seeded_event)
    assert await cached(container, event_id) is None

    response = await async_client.get(f"{API}/{event_id}")

    assert response.status_code == 200
    async with container.event_repository().load(event_id) as event_aggr:
        expected = EventSchemaMapper().to_event_detail(await event_aggr.layout())
    entry = await cached(container, event_id)
    assert entry.data == expected
    assert response.json() == expected.model_dump(mode="json")


async def test_unpublished_event_is_never_cached(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    event_id = seeded_event["event_id"]

    await async_client.get(f"{API}/{event_id}")
    await price_every_zone(async_client, seeded_event, price=2000)
    await async_client.patch(f"{ADMIN_API}/{event_id}/profile", json={"name": "Renamed", "description": "d"})

    assert await cached(container, event_id) is None


async def test_unknown_event_is_404_and_caches_nothing(
    async_client: AsyncClient, container: Container
):
    response = await async_client.get(f"{API}/999")

    assert response.status_code == 404
    assert await cached(container, 999) is None


async def test_db_change_shows_up_after_the_soft_expiry(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    await publish(async_client, seeded_event)
    await async_client.get(f"{API}/{event_id}")
    async with container.db().get_async_session() as session:
        await session.execute(
            text("UPDATE event SET name = 'Changed in DB' WHERE id = :id"), {"id": event_id}
        )
        await session.commit()
    await make_detail_stale(container, event_id)

    first = await async_client.get(f"{API}/{event_id}")
    second = await async_client.get(f"{API}/{event_id}")

    assert first.json()["name"] == "World Tour 2026"
    assert second.json()["name"] == "Changed in DB"


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
async def test_profile_update_rebuilds_the_detail(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    await publish(async_client, seeded_event)
    await async_client.get(f"{API}/{event_id}")

    response = await async_client.patch(
        f"{ADMIN_API}/{event_id}/profile", json={"name": "Renamed", "description": "d"}
    )
    assert response.status_code == 200

    assert (await cached(container, event_id)).data.name == "Renamed"
    assert (await async_client.get(f"{API}/{event_id}")).json()["name"] == "Renamed"


async def test_zone_price_change_rebuilds_the_detail(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    await publish(async_client, seeded_event)
    await async_client.get(f"{API}/{event_id}")

    await price_every_zone(async_client, seeded_event, price=2000)

    prices = {zone.price for zone in (await cached(container, event_id)).data.zones}
    assert prices == {2000}


async def test_unpublish_drops_the_detail(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    await publish(async_client, seeded_event)
    await async_client.get(f"{API}/{event_id}")

    response = await async_client.post(f"{ADMIN_API}/{event_id}/unpublish")
    assert response.status_code == 200

    assert await cached(container, event_id) is None
    assert (await async_client.get(f"{API}/{event_id}")).status_code == 404
    assert await cached(container, event_id) is None


async def test_delete_drops_the_detail(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    await publish(async_client, seeded_event)
    await async_client.get(f"{API}/{event_id}")

    response = await async_client.delete(f"{ADMIN_API}/{event_id}")
    assert response.status_code == 204

    assert await cached(container, event_id) is None
    assert (await async_client.get(f"{API}/{event_id}")).status_code == 404


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
async def test_detail_has_a_ten_minute_ttl_and_a_five_minute_soft_expiry(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    await publish(async_client, seeded_event)
    before = now_utc()

    await async_client.get(f"{API}/{event_id}")

    ttl = await container.redis_client().ttl(f"event:detail:{event_id}")
    assert 590 < ttl <= 600
    entry = await cached(container, event_id)
    assert before + timedelta(seconds=299) < entry.expired_at <= now_utc() + timedelta(seconds=300)


async def test_sync_keeps_a_published_detail_and_drops_an_unpublished_one(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    cache = container.event_detail_cache()
    async with container.event_repository().load(event_id) as event_aggr:
        detail = EventSchemaMapper().to_event_detail(await event_aggr.layout())
    await cache.set(detail.model_copy(update={"is_active": True}))

    await cache.sync(detail)
    assert await cached(container, event_id) is None

    await cache.sync(detail.model_copy(update={"is_active": True}))
    assert (await cached(container, event_id)).data.is_active is True


async def test_each_event_has_its_own_refresh_lock(container: Container):
    cache = container.event_detail_cache()

    assert await cache.acquire_refresh_lock(1) is True
    assert await cache.acquire_refresh_lock(2) is True
    assert await cache.acquire_refresh_lock(1) is False
    assert 0 < await container.redis_client().ttl("event:detail:1:refresh_lock") <= 10

    await cache.release_refresh_lock(1)
    assert await cache.acquire_refresh_lock(1) is True
