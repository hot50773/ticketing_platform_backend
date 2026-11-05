import logging
from datetime import timedelta
from uuid import uuid4

from redis import RedisError

from app.db.redis import RedisClient
from app.libs.clock.functional import now_utc
from app.schemas import Page, PaginationParams
from app.schemas.event import EventListCacheFieldInternal, EventResponse

logger = logging.getLogger(__name__)

# https://share.google/aimode/b0EdKnVYQOMXnhTXK
class EventListCache:
    """Caches event list response in Redis."""

    EVENT_LIST_KEY = "event:list:selling" # 活動列表
    TEMP_EVENT_LIST_KEY = "event:list:selling:temp"
    REFRESH_LOCK_KEY = "event:list:selling:refresh_lock"
    FIELD_KEY = "page_{page}:size_{size}"
    EXPIRE_SECONDS = 60 * 5
    SOFT_EXPIRE_SECONDS = 60 * 3
    LOCK_SECONDS = 10

    def __init__(self, redis_client: RedisClient):
        self._redis_client = redis_client

    @classmethod
    def generate_field_name(cls, page: int, page_size: int) -> str:
        return cls.FIELD_KEY.format(size=page_size, page=page)

    async def refresh_all(self, pages: list[Page[EventResponse]]):
        # One temp key per rebuild: concurrent rebuilds sharing one would RENAME it away from each other
        temp_key = f"{self.TEMP_EVENT_LIST_KEY}:{uuid4().hex}"
        permanent_key = self.EVENT_LIST_KEY
        expired_at = now_utc() + timedelta(seconds=self.SOFT_EXPIRE_SECONDS)

        async with self._redis_client.pipeline(transaction=True) as pipe:
            for page in pages:
                pipe.hset(
                    name=temp_key,
                    key=self.generate_field_name(page.page, page.page_size),
                    value=EventListCacheFieldInternal(data=page, expired_at=expired_at).model_dump_json(),
                )
            pipe.rename(temp_key, permanent_key)
            pipe.expire(permanent_key, self.EXPIRE_SECONDS)
            await pipe.execute()

    async def acquire_refresh_lock(self) -> bool:
        return bool(await self._redis_client.set(self.REFRESH_LOCK_KEY, 1, nx=True, ex=self.LOCK_SECONDS))

    async def release_refresh_lock(self) -> None:
        await self._redis_client.delete(self.REFRESH_LOCK_KEY)

    async def delete_all(self) -> None:
        try:
            await self._redis_client.delete(self.EVENT_LIST_KEY)
        except RedisError:
            logger.exception("Failed to delete event list")

    async def add(self, pagination: PaginationParams, input_data: EventListCacheFieldInternal) -> None:
        field_name = self.generate_field_name(pagination.page, pagination.page_size)
        await self._redis_client.hset(self.EVENT_LIST_KEY, key=field_name, value=input_data.model_dump_json())

    async def get_paged_events(self, pagination: PaginationParams) -> EventListCacheFieldInternal | None:
        field_name = self.generate_field_name(pagination.page, pagination.page_size)

        result = await self._redis_client.hget(self.EVENT_LIST_KEY, field_name)
        return EventListCacheFieldInternal.model_validate_json(result) if result else None
