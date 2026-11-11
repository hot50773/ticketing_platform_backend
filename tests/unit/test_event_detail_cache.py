"""Event detail cache: the soft-expiry read path and the rebuild after each write."""
from datetime import timedelta

import pytest
from redis import RedisError

from app.exceptions import ActiveOrdersExistError, EntityNotFoundError
from app.libs.clock.functional import now_utc
from app.schemas.event import (
    EventDetailCacheInternal,
    EventDetailResponse,
    EventProfileUpdate,
)
from app.services.event import EventService
from app.services.mappers import EventSchemaMapper
from app.services.validators import EventRuleValidator
from tests.unit._fakes import (
    FakeBackgroundTasks,
    FakeEventDetailCache,
    FakeEventLayoutCache,
    FakeEventListCache,
    FakeEventRepository,
    an_event,
)


def a_service(repository: FakeEventRepository, detail_cache: FakeEventDetailCache) -> EventService:
    return EventService(
        event_repository=repository,
        event_rule_validator=EventRuleValidator(),
        event_schema_mapper=EventSchemaMapper(),
        seat_cache=FakeEventLayoutCache(repository.calls),
        event_list_cache=FakeEventListCache(repository.calls),
        event_detail_cache=detail_cache,
    )


def a_cached_detail(expires_in: timedelta) -> EventDetailCacheInternal:
    """A cached event renamed "Cached", so a test can tell it from a DB read."""
    event = an_event(is_active=True)
    event.name = "Cached"
    detail = EventDetailResponse(**EventSchemaMapper().to_event(event).model_dump(), zones=[])
    return EventDetailCacheInternal(data=detail, expired_at=now_utc() + expires_in)


FRESH = timedelta(seconds=30)
STALE = -timedelta(seconds=1)


# --------------------------------------------------------------------------- #
# Read path
# --------------------------------------------------------------------------- #
async def test_fresh_detail_is_served_without_touching_the_db():
    repo = FakeEventRepository()
    cache = FakeEventDetailCache(repo.calls, entry=a_cached_detail(FRESH))
    background = FakeBackgroundTasks()

    result = await a_service(repo, cache).get_event(1, background)

    assert result.name == "Cached"
    assert repo.calls == []
    assert background.added == []


async def test_stale_detail_is_served_and_rebuilt_after_the_response():
    repo = FakeEventRepository(is_active=True)
    cache = FakeEventDetailCache(repo.calls, entry=a_cached_detail(STALE))
    background = FakeBackgroundTasks()

    result = await a_service(repo, cache).get_event(1, background)
    assert result.name == "Cached"
    assert repo.calls == []

    await background.run()
    assert repo.calls == [
        "acquire_detail_lock", "load", "layout", "set_detail", "release_detail_lock",
    ]
    assert cache.stored[1].name == "World Tour"
    assert cache.stored[1].zones[0].seat_count == 4


async def test_stale_detail_is_not_rebuilt_while_another_holds_the_lock():
    repo = FakeEventRepository()
    cache = FakeEventDetailCache(repo.calls, entry=a_cached_detail(STALE), lock_free=False)
    background = FakeBackgroundTasks()

    await a_service(repo, cache).get_event(1, background)
    await background.run()

    assert repo.calls == ["acquire_detail_lock"]


async def test_miss_reads_the_db_and_fills_the_cache_after_the_response():
    repo = FakeEventRepository(is_active=True)
    cache = FakeEventDetailCache(repo.calls, entry=None)
    background = FakeBackgroundTasks()

    result = await a_service(repo, cache).get_event(1, background)
    assert result.name == "World Tour"
    assert repo.calls == ["load", "layout"]

    await background.run()
    assert "set_detail" in repo.calls
    assert cache.stored[1].name == "World Tour"


async def test_an_unpublished_event_reads_as_missing_and_caches_nothing():
    """A draft's name, date and prices are not public yet."""
    repo = FakeEventRepository(is_active=False)
    cache = FakeEventDetailCache(repo.calls, entry=None)
    background = FakeBackgroundTasks()

    with pytest.raises(EntityNotFoundError):
        await a_service(repo, cache).get_event(1, background)

    assert background.added == []


async def test_managers_read_drafts_straight_from_the_database():
    repo = FakeEventRepository(is_active=False)
    cache = FakeEventDetailCache(repo.calls, entry=None)

    result = await a_service(repo, cache).get_event_for_manager(1)

    assert result.is_active is False
    assert repo.calls == ["load", "layout"]


async def test_stale_detail_of_an_unpublished_event_is_dropped_instead_of_rebuilt():
    """A miss refill racing the unpublish can leave a published copy behind."""
    repo = FakeEventRepository(is_active=False)
    cache = FakeEventDetailCache(repo.calls, entry=a_cached_detail(STALE))
    background = FakeBackgroundTasks()

    await a_service(repo, cache).get_event(1, background)
    await background.run()

    assert cache.deleted == [1]
    assert "set_detail" not in repo.calls
    assert not cache.lock_held


async def test_unknown_event_is_404_and_caches_nothing():
    repo = FakeEventRepository(missing=True)
    cache = FakeEventDetailCache(repo.calls, entry=None)
    background = FakeBackgroundTasks()

    with pytest.raises(EntityNotFoundError):
        await a_service(repo, cache).get_event(1, background)

    assert background.added == []


async def test_failed_rebuild_raises_but_keeps_the_lock():
    repo = FakeEventRepository(is_active=True)
    cache = FakeEventDetailCache(
        repo.calls, entry=a_cached_detail(STALE), set_error=RedisError("down")
    )
    background = FakeBackgroundTasks()

    await a_service(repo, cache).get_event(1, background)
    with pytest.raises(RedisError):
        await background.run()

    assert "release_detail_lock" not in repo.calls
    assert cache.lock_held


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
async def test_profile_update_of_a_published_event_rebuilds_the_detail_after_the_response():
    repo = FakeEventRepository(is_active=True)
    cache = FakeEventDetailCache(repo.calls)
    background = FakeBackgroundTasks()

    await a_service(repo, cache).update_event_profile(
        1, EventProfileUpdate(name="Renamed", description="d"), background
    )
    assert "set_detail" not in repo.calls

    await background.run()
    assert "acquire_detail_lock" not in repo.calls
    assert 1 in cache.stored


async def test_profile_update_of_an_unpublished_event_leaves_the_cache_alone():
    repo = FakeEventRepository(is_active=False)
    cache = FakeEventDetailCache(repo.calls)
    background = FakeBackgroundTasks()

    await a_service(repo, cache).update_event_profile(
        1, EventProfileUpdate(name="Renamed", description="d"), background
    )

    assert background.added == []


async def test_publish_leaves_the_detail_to_the_first_read():
    repo = FakeEventRepository(is_active=False)
    cache = FakeEventDetailCache(repo.calls)
    background = FakeBackgroundTasks()

    await a_service(repo, cache).publish_event(1, background)
    await background.run()

    assert cache.stored == {}
    assert cache.deleted == []


async def test_unpublish_and_delete_drop_the_detail():
    for write in [
        lambda service, bg: service.unpublish_event(1, bg),
        lambda service, bg: service.delete_event(1, bg),
    ]:
        repo = FakeEventRepository(is_active=True)
        cache = FakeEventDetailCache(repo.calls)
        background = FakeBackgroundTasks()

        await write(a_service(repo, cache), background)
        await background.run()

        assert cache.deleted == [1]
        assert "set_detail" not in repo.calls


async def test_rejected_write_schedules_nothing():
    repo = FakeEventRepository(order_count=1)
    cache = FakeEventDetailCache(repo.calls)
    background = FakeBackgroundTasks()

    with pytest.raises(ActiveOrdersExistError):
        await a_service(repo, cache).delete_event(1, background)

    assert background.added == []


async def test_write_succeeds_before_its_detail_rebuild_fails():
    repo = FakeEventRepository(is_active=True)
    cache = FakeEventDetailCache(repo.calls, set_error=RedisError("down"))
    background = FakeBackgroundTasks()

    result = await a_service(repo, cache).update_event_profile(
        1, EventProfileUpdate(name="Renamed", description="d"), background
    )
    with pytest.raises(RedisError):
        await background.run()

    assert result.id == 1
