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
- V 讀取時超過 5 分鐘軟過期就背景刷新，10 分鐘 TTL 保底
"""
import logging
from typing import TYPE_CHECKING

from app.cache.event_layout import EventLayoutCache
from app.cache.event_list import EventListCache
from app.constants import MAX_CACHE_PAGES
from app.enums import EventListMode, PageSize
from app.libs.clock.functional import now_utc
from app.repositories.event import EventRepository
from app.schemas import Page, PaginationParamsInternal, PaginationParams
from app.schemas.event import (
    EventCreate,
    EventDetailResponse,
    EventHoldingTimeUpdate,
    EventProfileUpdate,
    EventResponse,
)
from app.services.mappers import EventSchemaMapper
from app.services.validators import EventRuleValidator
if TYPE_CHECKING:
    from fastapi import BackgroundTasks

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

    async def list_selling_events(
        self, pagination: PaginationParams, background_tasks: "BackgroundTasks"
    ) -> Page[EventResponse]:
        """A stale page is still served; the refresh it triggers runs after the response."""
        cached = None
        if pagination.page <= MAX_CACHE_PAGES:
            cached = await self._event_list_cache.get_paged_events(pagination)
            if cached is None or cached.expired_at <= now_utc():
                background_tasks.add_task(self.rebuild_selling_events_cache)

        if cached:
            result = cached.data
        else:
            logger.info("list_selling_events cache miss: %s", pagination.model_dump())
            result = await self._list_selling_events(pagination)
        return result

    async def _list_selling_events(self, pagination: PaginationParamsInternal) -> Page[EventResponse]:
        paged_list = await self._repository.get_events(pagination, EventListMode.SELLING)
        return self._mapper.to_event_page(paged_list)

    async def rebuild_selling_events_cache(self) -> None:
        """A failed rebuild skips the release, so the lock doubles as a backoff."""
        if await self._event_list_cache.acquire_refresh_lock():
            await self._rebuild_selling_events_cache()
            await self._event_list_cache.release_refresh_lock()

    async def _rebuild_selling_events_cache(self) -> None:
        n = MAX_CACHE_PAGES * max(PageSize)
        first_n_events = await self._list_selling_events(
            PaginationParamsInternal(page=1, page_size=n)
        )
        pages: list[Page[EventResponse]] = []
        for size in PageSize:
            for page in range(1, MAX_CACHE_PAGES + 1):
                offset = PaginationParams(page=page, page_size=size).offset
                sliced_items = first_n_events.items[offset : offset + size]
                pages.append(
                    Page[EventResponse](
                        items=sliced_items,
                        total=first_n_events.total,
                        page=page,
                        page_size=size,
                    )
                )
        await self._event_list_cache.refresh_all(pages)

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

    async def publish_event(self, event_id: int, background_tasks: "BackgroundTasks") -> EventResponse:
        async with self._repository.load_for_update(event_id) as event_aggr:
            event_layout = await event_aggr.event_layout()
            self._validator.validate_publishable(event_layout.zones)

            await self._seat_cache.init_event_layouts(event_layout)
            await event_aggr.publish()
            response = self._mapper.to_event(event_aggr.event)
        # TODO: 改成 event driven
        background_tasks.add_task(self._rebuild_selling_events_cache)
        return response

    async def unpublish_event(self, event_id: int, background_tasks: "BackgroundTasks") -> EventResponse:
        """The cache should be cleared *after* the transaction commits"""
        async with self._repository.load_for_update(event_id) as event_aggr:
            self._validator.validate_no_active_order(await event_aggr.order_stats())
            zone_ids = await event_aggr.zone_ids()
            await event_aggr.unpublish()
            response = self._mapper.to_event(event_aggr.event)

        # TODO: 這個可以拆分成 method
        # TODO: 改成 event driven
        await self._seat_cache.clear_seat_layouts(event_id, zone_ids)
        background_tasks.add_task(self._rebuild_selling_events_cache)
        return response

    async def delete_event(self, event_id: int, background_tasks: "BackgroundTasks") -> None:
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
        background_tasks.add_task(self._rebuild_selling_events_cache)
