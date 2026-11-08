"""Selling list cache: the 2-query rebuild and the soft-expiry refresh on read.

A read hands its refresh to a background runner; the tests run it themselves,
the way FastAPI does once the response is sent.
"""
from datetime import timedelta

import pytest
from redis import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.libs.clock.functional import now_utc
from app.schemas import Page, PaginationParams
from app.schemas.event import EventListCacheFieldInternal
from app.services.event import EventService
from app.services.mappers import EventSchemaMapper
from app.services.validators import EventRuleValidator
from tests.unit._fakes import (
    FakeBackgroundTasks,
    FakeEventLayoutCache,
    FakeEventListCache,
    FakeEventRepository,
    an_event,
)


def a_service(repository: FakeEventRepository, list_cache: FakeEventListCache) -> EventService:
    return EventService(
        event_repository=repository,
        event_rule_validator=EventRuleValidator(),
        event_schema_mapper=EventSchemaMapper(),
        seat_cache=FakeEventLayoutCache(repository.calls),
        event_list_cache=list_cache,
    )


def a_cached_page(expires_in: timedelta) -> EventListCacheFieldInternal:
    """One cached event (id 7), so a test can tell a cached page from a DB read."""
    event = EventSchemaMapper().to_event(an_event(is_active=True, event_id=7))
    return EventListCacheFieldInternal(
        data=Page(items=[event], total=1, page=1, page_size=20),
        expired_at=now_utc() + expires_in,
    )


FRESH = timedelta(seconds=30)
STALE = -timedelta(seconds=1)


def ids_by_page(pages: list[Page]) -> dict[tuple[int, int], list[int]]:
    return {(page.page_size, page.page): [item.id for item in page.items] for page in pages}


# --------------------------------------------------------------------------- #
# Rebuild
# --------------------------------------------------------------------------- #
async def test_rebuild_slices_one_query_into_every_cached_page():
    repo = FakeEventRepository(selling_total=23)
    cache = FakeEventListCache(repo.calls)

    await a_service(repo, cache)._rebuild_selling_events_cache()

    assert ids_by_page(cache.refreshed) == {
        (20, 1): list(range(1, 21)),
        (20, 2): [21, 22, 23],
        (20, 3): [], (20, 4): [], (20, 5): [],
        (50, 1): list(range(1, 24)),
        (50, 2): [], (50, 3): [], (50, 4): [], (50, 5): [],
    }
    assert {page.total for page in cache.refreshed} == {23}
    assert len(repo.list_reads) == 1


async def test_rebuild_caches_only_the_first_five_pages_but_counts_everything():
    repo = FakeEventRepository(selling_total=300)
    cache = FakeEventListCache(repo.calls)

    await a_service(repo, cache)._rebuild_selling_events_cache()

    pages = ids_by_page(cache.refreshed)
    assert pages[(50, 5)] == list(range(201, 251))
    assert pages[(20, 5)] == list(range(81, 101))
    assert {page.total for page in cache.refreshed} == {300}


async def test_rebuild_with_nothing_on_sale_still_writes_every_page():
    """An empty page has to be cached too, or every read of it misses."""
    repo = FakeEventRepository(selling_total=0)
    cache = FakeEventListCache(repo.calls)

    await a_service(repo, cache)._rebuild_selling_events_cache()

    assert ids_by_page(cache.refreshed) == {
        (20, 1): [], (20, 2): [], (20, 3): [], (20, 4): [], (20, 5): [],
        (50, 1): [], (50, 2): [], (50, 3): [], (50, 4): [], (50, 5): [],
    }
    assert {page.total for page in cache.refreshed} == {0}


# --------------------------------------------------------------------------- #
# Read path
# --------------------------------------------------------------------------- #
async def test_fresh_cache_is_served_without_touching_the_db_or_refreshing():
    repo = FakeEventRepository(selling_total=5)
    cache = FakeEventListCache(repo.calls, entry=a_cached_page(FRESH))
    background = FakeBackgroundTasks()

    result = await a_service(repo, cache).list_selling_events(PaginationParams(), background)
    await background.run()

    assert [item.id for item in result.items] == [7]
    assert repo.list_reads == []
    assert background.added == []


async def test_stale_cache_is_still_served_and_refreshed_once():
    repo = FakeEventRepository(selling_total=5)
    cache = FakeEventListCache(repo.calls, entry=a_cached_page(STALE))
    background = FakeBackgroundTasks()

    result = await a_service(repo, cache).list_selling_events(PaginationParams(), background)
    await background.run()

    assert [item.id for item in result.items] == [7]
    assert len(background.added) == 1
    assert repo.calls == ["acquire_refresh_lock", "refresh_all", "release_refresh_lock"]
    assert ids_by_page(cache.refreshed)[(20, 1)] == [1, 2, 3, 4, 5]


async def test_stale_cache_is_not_refreshed_while_another_refresh_holds_the_lock():
    repo = FakeEventRepository(selling_total=5)
    cache = FakeEventListCache(repo.calls, entry=a_cached_page(STALE), lock_free=False)
    background = FakeBackgroundTasks()

    result = await a_service(repo, cache).list_selling_events(PaginationParams(), background)
    await background.run()

    assert [item.id for item in result.items] == [7]
    assert repo.calls == ["acquire_refresh_lock"]


async def test_cache_miss_reads_the_db_and_refreshes():
    """Without a cron, a key that expired unread only comes back through a read."""
    repo = FakeEventRepository(selling_total=5)
    cache = FakeEventListCache(repo.calls, entry=None)
    background = FakeBackgroundTasks()

    result = await a_service(repo, cache).list_selling_events(PaginationParams(), background)
    await background.run()

    assert [item.id for item in result.items] == [1, 2, 3, 4, 5]
    assert repo.calls == ["acquire_refresh_lock", "refresh_all", "release_refresh_lock"]


async def test_page_beyond_the_cache_reads_the_db_without_refreshing():
    repo = FakeEventRepository(selling_total=500)
    cache = FakeEventListCache(repo.calls, entry=None)
    background = FakeBackgroundTasks()

    await a_service(repo, cache).list_selling_events(PaginationParams(page=6), background)
    await background.run()

    assert [p.page for p in repo.list_reads] == [6]
    assert background.added == []


async def test_failed_refresh_raises_but_keeps_the_lock_as_a_backoff():
    """Releasing it would let every following read retry against a dead Redis."""
    repo = FakeEventRepository(selling_total=5)
    cache = FakeEventListCache(
        repo.calls, entry=a_cached_page(STALE), refresh_error=RedisError("down")
    )
    background = FakeBackgroundTasks()

    result = await a_service(repo, cache).list_selling_events(PaginationParams(), background)
    with pytest.raises(RedisError):
        await background.run()

    assert [item.id for item in result.items] == [7]
    assert repo.calls == ["acquire_refresh_lock", "refresh_all"]
    assert cache.lock_held


async def test_failed_db_read_during_refresh_raises_but_keeps_the_lock():
    repo = FakeEventRepository(list_error=SQLAlchemyError("db down"))
    cache = FakeEventListCache(repo.calls, entry=a_cached_page(STALE))
    background = FakeBackgroundTasks()

    await a_service(repo, cache).list_selling_events(PaginationParams(), background)
    with pytest.raises(SQLAlchemyError):
        await background.run()

    assert repo.calls == ["acquire_refresh_lock"]
    assert cache.lock_held


# --------------------------------------------------------------------------- #
# Admin writes
# --------------------------------------------------------------------------- #
async def test_publish_rebuilds_without_waiting_for_the_lock():
    """A refresh holding the lock may have read the DB before this commit."""
    repo = FakeEventRepository(selling_total=1)
    cache = FakeEventListCache(repo.calls, lock_free=False)

    background = FakeBackgroundTasks()

    await a_service(repo, cache).publish_event(1, background)
    await background.run()

    assert "acquire_refresh_lock" not in repo.calls
    assert ids_by_page(cache.refreshed)[(20, 1)] == [1]


async def test_publish_succeeds_before_its_rebuild_fails():
    """The rebuild runs after the response, so its error never reaches the admin."""
    repo = FakeEventRepository(selling_total=1)
    cache = FakeEventListCache(repo.calls, refresh_error=RedisError("down"))
    background = FakeBackgroundTasks()

    result = await a_service(repo, cache).publish_event(1, background)
    with pytest.raises(RedisError):
        await background.run()

    assert result.id == 1
    assert "publish" in repo.calls
