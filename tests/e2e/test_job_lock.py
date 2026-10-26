"""The lock that keeps the sweep to one runner (see SeatReleaseService.run)."""
import pytest
from redis import RedisError

from app.cache.job_lock import JobLock
from app.containers import Container


@pytest.fixture
def lock_factory(container: Container):
    def _make(ttl_seconds: int = 30, token: str | None = None) -> JobLock:
        return JobLock(
            redis_client=container.redis_client(),
            name="test_sweep",
            ttl_seconds=ttl_seconds,
            token_factory=(lambda: token) if token else None,
        )
    return _make


@pytest.mark.asyncio
async def test_only_one_runner_gets_in(lock_factory):
    async with lock_factory().held() as first:
        async with lock_factory().held() as second:
            assert first is True
            assert second is False


@pytest.mark.asyncio
async def test_the_next_round_can_start_once_the_last_one_finished(lock_factory):
    async with lock_factory().held():
        pass

    async with lock_factory().held() as acquired:
        assert acquired is True


@pytest.mark.asyncio
async def test_a_crash_mid_sweep_does_not_wedge_the_lock(lock_factory):
    with pytest.raises(RuntimeError):
        async with lock_factory().held():
            raise RuntimeError("sweep blew up")

    async with lock_factory().held() as acquired:
        assert acquired is True


@pytest.mark.asyncio
async def test_a_runner_that_lost_the_lock_does_not_take_it_from_the_next_one(
    container, lock_factory
):
    """The dangerous case: our TTL ran out, somebody else took the lock, and we
    finish. A plain DEL here would hand two runners the sweep at once."""
    redis_client = container.redis_client()

    async with lock_factory(token="ours").held():
        await redis_client.set("job_lock:test_sweep", "somebody_elses")

    assert await redis_client.get("job_lock:test_sweep") == "somebody_elses"


@pytest.mark.asyncio
async def test_the_lock_expires_on_its_own(container, lock_factory):
    """A runner killed outright leaves no finally block behind, so the lock has
    to let go by itself."""
    async with lock_factory(ttl_seconds=30).held():
        ttl = await container.redis_client().ttl("job_lock:test_sweep")

    assert 0 < ttl <= 30


@pytest.mark.asyncio
async def test_a_sweep_that_cannot_get_the_lock_reads_nothing(container):
    """Skipping means skipping - not querying and then doing nothing."""
    sweep = container.seat_release_service()
    calls = []
    sweep._orders = _RecordingRepository(calls)

    async with container.seat_release_lock().held() as held_by_someone_else:
        assert held_by_someone_else, "the test needs to hold the sweep's own lock"
        await sweep.run()

    assert calls == []


class _RecordingRepository:
    def __init__(self, calls: list[str]):
        self._calls = calls

    def __getattr__(self, name):
        async def _record(*args, **kwargs):
            self._calls.append(name)
            raise RedisError("the sweep should never have got here")
        return _record
