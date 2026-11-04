"""Event lifecycle use cases: create, read, update, publish, delete.

Every write use case enters the repository once, through an aggregate that
owns a single transaction. Reading state and writing the decision that depends
on it therefore happen together; see app/aggregates/event.py for why the write
paths also take a row lock.

Boundaries:
  - persistence goes through the aggregate, never a session
  - domain rules go through EventRuleValidator, which stays I/O free
  - ORM -> schema translation goes through EventSchemaMapper
  - the zone / seat layout lives in EventLayoutService, not here
  - seat bitmaps: publish_event warms them, unpublish_event and delete_event
    drop them again
  - selling list cache: those three rebuild it after commit, best-effort, since
    the event is already saved and the next rebuild repairs a stale list

event cache - 實作活動列表的快取，在大流量瘋狂搶票時，把讀取活動列表的流量擋下來，減輕資料庫負荷。
TODO:
- V event list 分流
- V event list cache 刷新
- V event list cache 讀取 by filter
- 每 10 分鐘刷新 cache
"""
import logging

from redis import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.cache.event_layout import EventLayoutCache
from app.cache.event_list import EventListCache
from app.constants import MAX_CACHE_PAGES
from app.enums import EventListMode, PageSize
from app.repositories.event import EventRepository
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    EventCreate,
    EventDetailResponse,
    EventHoldingTimeUpdate,
    EventProfileUpdate,
    EventResponse,
)
from app.services.mappers import EventSchemaMapper
from app.services.validators import EventRuleValidator

logger = logging.getLogger(__name__)


class EventService:
    """Orchestrates the lifecycle of an event."""

    def __init__(
        self,
        event_repository: EventRepository,
        event_list_cache: EventListCache,
        seat_cache: EventLayoutCache,
        event_rule_validator: EventRuleValidator,
        event_schema_mapper: EventSchemaMapper,
    ):
        self._repository = event_repository
        self._validator = event_rule_validator
        self._mapper = event_schema_mapper
        self._seat_cache = seat_cache
        self._event_list_cache = event_list_cache

    async def create_event(self, payload: EventCreate) -> EventDetailResponse:
        """The event is always created unpublished with every zone price unset."""
        self._validator.validate_holding_time(payload)
        async with self._repository.create(payload) as event_aggr:
            return self._mapper.to_event_detail(await event_aggr.layout())

    async def list_events(self, pagination: PaginationParams) -> Page[EventResponse]:
        """One page of non-deleted events."""
        paged_list = await self._repository.get_events(pagination)
        return self._mapper.to_event_page(paged_list)

    async def list_selling_events(self, pagination: PaginationParams) -> Page[EventResponse]:
        # get from cache first
        if cache_result := (await self._event_list_cache.get_paged_events(pagination)):
            return cache_result.data

        logger.info("list_selling_events cache miss: %s", pagination.model_dump())
        return await self._list_selling_events(pagination)

    async def _list_selling_events(self, pagination: PaginationParams) -> Page[EventResponse]:
        paged_list = await self._repository.get_events(pagination, EventListMode.SELLING)
        return self._mapper.to_event_page(paged_list)

    async def _reset_event_list_cache(self, silence: bool = True) -> None:
        try:
            pages: list[Page[EventResponse]] = []
            for size in PageSize:
                for page in range(1, MAX_CACHE_PAGES + 1):
                    pagination = PaginationParams(page=page, page_size=size)
                    pages.append(await self._list_selling_events(pagination))

            await self._event_list_cache.refresh_all(pages)
        # TODO: repository 應把 DB 錯誤包成 app/exceptions.py 的自訂例外，service 就不必 import sqlalchemy
        except (RedisError, SQLAlchemyError) as e:
            logger.exception("Failed to reset event list cache")
            if not silence:
                raise e

    async def get_event(self, event_id: int) -> EventDetailResponse:
        """Returns an event with its zones (seat counts only, not the seats)."""
        async with self._repository.load(event_id) as event_aggr:
            return self._mapper.to_event_detail(await event_aggr.layout())

    async def update_event_profile(
        self, event_id: int, payload: EventProfileUpdate
    ) -> EventResponse:
        """Updates name / description. Allowed while the event is published."""
        async with self._repository.load_for_update(event_id) as event_aggr:
            await event_aggr.update_profile(payload)
            return self._mapper.to_event(event_aggr.event)

    async def update_event_holding_time(
        self, event_id: int, payload: EventHoldingTimeUpdate
    ) -> EventResponse:
        async with self._repository.load_for_update(event_id) as event_aggr:
            self._validator.validate_unpublished(event_aggr.state)
            self._validator.validate_holding_time(payload)
            await event_aggr.set_holding_time(payload)
            return self._mapper.to_event(event_aggr.event)

    async def publish_event(self, event_id: int) -> EventResponse:
        async with self._repository.load_for_update(event_id) as event_aggr:
            event_layout = await event_aggr.event_layout()
            self._validator.validate_publishable(event_layout.zones)

            await self._seat_cache.init_event_layouts(event_layout)
            await event_aggr.publish()
            response = self._mapper.to_event(event_aggr.event)
        # TODO: 改成 event driven
        await self._reset_event_list_cache()
        return response

    async def unpublish_event(self, event_id: int) -> EventResponse:
        """The cache should be cleared *after* the transaction commits, the mirror image
        of publish_event. Dropping a still-live event's bitmap would make every
        seat read as non-existent, so it must not happen until the event is
        certainly off sale; a bitmap left behind by a failed clear is inert.
        """
        async with self._repository.load_for_update(event_id) as event_aggr:
            self._validator.validate_no_active_order(await event_aggr.order_stats())
            zone_ids = await event_aggr.zone_ids()
            await event_aggr.unpublish()
            response = self._mapper.to_event(event_aggr.event)

        # TODO: 這個可以拆分成 method
        # TODO: 改成 event driven
        await self._seat_cache.clear_seat_layouts(event_id, zone_ids)
        await self._reset_event_list_cache()
        return response

    async def delete_event(self, event_id: int) -> None:
        """The bitmaps are cleared after the commit, as in unpublish_event.

        The delete hands back the zone ids it removed; querying for them
        afterwards would come back empty.
        """
        async with self._repository.load_for_update(event_id) as event_aggr:
            self._validator.validate_no_active_order(await event_aggr.order_stats())
            zone_ids = await event_aggr.soft_delete_cascade()

        # TODO: 這個可以拆分成 method
        # TODO: 改成 event driven
        await self._seat_cache.clear_seat_layouts(event_id, zone_ids)
        await self._reset_event_list_cache()
