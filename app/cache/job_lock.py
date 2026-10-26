"""Keeps a scheduled job to one runner at a time.

An operational guarantee, not a structural one: if the TTL runs out while the
job is still working, a second runner starts and both are live. The job itself
has to stay correct under that - this only makes it rare.
"""
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from redis import RedisError

from app.db.redis import RedisClient

logger = logging.getLogger(__name__)

RELEASE_SCRIPT_PATH = Path(__file__).parent / "lua" / "release_job_lock.lua"
DEFAULT_TTL_SECONDS = 60


class JobLock:
    KEY = "job_lock:{name}"

    def __init__(
        self,
        redis_client: RedisClient,
        name: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        token_factory: Callable[[], str] | None = None,
    ):
        self._redis_client = redis_client
        self._key = self.KEY.format(name=name)
        self._ttl_seconds = ttl_seconds
        self._new_token = token_factory or (lambda: uuid.uuid4().hex)
        self._release_script = redis_client.register_script(
            RELEASE_SCRIPT_PATH.read_text(encoding="utf-8")
        )

    @asynccontextmanager
    async def held(self) -> AsyncIterator[bool]:
        """Yields whether this runner got the lock. Releases it on the way out,
        and only if it is still the one we took."""
        token = self._new_token()
        acquired = bool(
            await self._redis_client.set(
                self._key, token, nx=True, ex=self._ttl_seconds
            )
        )
        try:
            yield acquired
        finally:
            if acquired:
                await self._release(token)

    async def _release(self, token: str) -> None:
        try:
            freed = await self._release_script(keys=[self._key], args=[token])
            if not freed:
                logger.warning(
                    "%s was already somebody else's by the time we finished; "
                    "the TTL ran out mid-run",
                    self._key,
                )
        except RedisError:
            logger.exception("Could not release %s; it expires in %ss",
                             self._key, self._ttl_seconds)
