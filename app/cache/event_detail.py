from datetime import timedelta

from app.db.redis import RedisClient
from app.libs.clock.functional import now_utc
from app.schemas.event import EventDetailCacheInternal, EventDetailResponse


class EventDetailCache:
    """Caches each event's detail response in Redis, one key per event."""

    KEY = "event:detail:{event_id}"
    REFRESH_LOCK_KEY = "event:detail:{event_id}:refresh_lock"
    EXPIRE_SECONDS = 60 * 10
    SOFT_EXPIRE_SECONDS = 60 * 5
    LOCK_SECONDS = 10

    def __init__(self, redis_client: RedisClient):
        self._redis_client = redis_client

    async def get(self, event_id: int) -> EventDetailCacheInternal | None:
        result = await self._redis_client.get(self.KEY.format(event_id=event_id))
        return EventDetailCacheInternal.model_validate_json(result) if result else None

    async def set(self, detail: EventDetailResponse) -> None:
        expired_at = now_utc() + timedelta(seconds=self.SOFT_EXPIRE_SECONDS)
        await self._redis_client.set(
            self.KEY.format(event_id=detail.id),
            EventDetailCacheInternal(data=detail, expired_at=expired_at).model_dump_json(),
            ex=self.EXPIRE_SECONDS,
        )

    async def delete(self, event_id: int) -> None:
        await self._redis_client.delete(self.KEY.format(event_id=event_id))

    async def sync(self, detail: EventDetailResponse) -> None:
        """Only published events are cached."""
        if detail.is_active:
            await self.set(detail)
        else:
            await self.delete(detail.id)

    async def acquire_refresh_lock(self, event_id: int) -> bool:
        key = self.REFRESH_LOCK_KEY.format(event_id=event_id)
        return bool(await self._redis_client.set(key, 1, nx=True, ex=self.LOCK_SECONDS))

    async def release_refresh_lock(self, event_id: int) -> None:
        await self._redis_client.delete(self.REFRESH_LOCK_KEY.format(event_id=event_id))
