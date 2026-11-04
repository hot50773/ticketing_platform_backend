import logging

from redis import RedisError

from app.db.redis import RedisClient
from app.schemas import Page, PaginationParams
from app.schemas.event import EventListCacheFieldInternal, EventResponse

logger = logging.getLogger(__name__)

# https://share.google/aimode/b0EdKnVYQOMXnhTXK
class EventListCache:
    """Caches event list response in Redis."""

    EVENT_LIST_KEY = "event:list:selling" # 活動列表
    TEMP_EVENT_LIST_KEY = "event:list:selling:temp"
    FIELD_KEY = "page_{page}:size_{size}"
    EXPIRE_SECONDS = 60 * 60 * 24 * 7 # 7 days

    def __init__(self, redis_client: RedisClient):
        self._redis_client = redis_client

    @classmethod
    def generate_field_name(cls, page: int, page_size: int) -> str:
        return cls.FIELD_KEY.format(size=page_size, page=page)

    async def refresh_all(self, pages: list[Page[EventResponse]]):
        temp_key = self.TEMP_EVENT_LIST_KEY
        permanent_key = self.EVENT_LIST_KEY

        async with self._redis_client.pipeline() as pipe:
            for page in pages:
                pipe.hset(
                    name=temp_key,
                    key=self.generate_field_name(page.page, page.page_size),
                    value=EventListCacheFieldInternal(data=page).model_dump_json(),
                )
            await pipe.execute()

        await self._redis_client.rename(temp_key, permanent_key)
        await self._redis_client.expire(permanent_key, self.EXPIRE_SECONDS)

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
