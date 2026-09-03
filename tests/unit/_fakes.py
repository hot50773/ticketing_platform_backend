"""Fake aggregate and repository for service-level unit tests.

Both share one `calls` list, so a test can assert on the exact sequence of
things a use case did - including what it did *not* do after a rule failed.
"""
from contextlib import asynccontextmanager
from datetime import timedelta

from app.libs.clock.functional import now_utc
from app.models import Event, EventZone
from app.schemas import Page
from app.schemas.event import (
    EventLayoutSummaryInternal,
    EventOrderStatsInternal,
    EventSeatSyncCountsInternal,
    EventStatusInternal,
    EventZonePricingInternal,
    EventZoneWithSeatCountInternal,
)

START = now_utc() + timedelta(days=30)


def an_event(is_active: bool = False) -> Event:
    return Event(
        id=1,
        name="World Tour",
        description="d",
        start_at=START,
        end_at=START + timedelta(hours=3),
        is_active=is_active,
        venue_id=1,
        created_at=START,
        updated_at=START,
    )


def a_zone(name: str = "A", price: int | None = None) -> EventZone:
    return EventZone(
        id=10, event_id=1, name=name, price=price, created_at=START, updated_at=START
    )


class FakeAggregate:
    def __init__(self, calls: list[str], event: Event, order_count: int, occupied: int):
        self.calls = calls
        self._event = event
        self._order_count = order_count
        self._occupied = occupied

    @property
    def event(self) -> Event:
        return self._event

    @property
    def status(self) -> EventStatusInternal:
        return EventStatusInternal(id=self._event.id, is_active=self._event.is_active)

    async def layout(self) -> EventLayoutSummaryInternal:
        self.calls.append("layout")
        return EventLayoutSummaryInternal(
            event=self._event,
            zones=[EventZoneWithSeatCountInternal(zone=a_zone(), seat_count=4)],
        )

    async def zone_pricings(self) -> list[EventZonePricingInternal]:
        self.calls.append("zone_pricings")
        return [EventZonePricingInternal(id=10, name="A", price=1500)]

    async def order_stats(self) -> EventOrderStatsInternal:
        self.calls.append("order_stats")
        return EventOrderStatsInternal(active_order_count=self._order_count)

    async def occupied_seat_count(self, event_zone_id: int) -> int:
        self.calls.append("occupied_seat_count")
        return self._occupied

    async def zone_seats(self, event_zone_id: int, pagination) -> Page:
        self.calls.append("zone_seats")
        return Page(items=[], total=0, limit=pagination.limit, offset=pagination.offset)

    async def update_profile(self, payload) -> None:
        self.calls.append("update_profile")

    async def set_holding_time(self, payload) -> None:
        self.calls.append("set_holding_time")

    async def set_status(self, is_active: bool) -> None:
        self.calls.append("set_status")

    async def soft_delete(self) -> None:
        self.calls.append("soft_delete")

    async def add_zone(self, payload) -> EventZoneWithSeatCountInternal:
        self.calls.append("add_zone")
        return EventZoneWithSeatCountInternal(
            zone=a_zone(payload.name, payload.price), seat_count=len(payload.seats)
        )

    async def update_zone_profile(self, event_zone_id, payload):
        self.calls.append("update_zone_profile")
        return EventZoneWithSeatCountInternal(
            zone=a_zone(payload.name, payload.price), seat_count=2
        )

    async def sync_zone_seats(self, event_zone_id, seats) -> EventSeatSyncCountsInternal:
        self.calls.append("sync_zone_seats")
        return EventSeatSyncCountsInternal(created_count=1, deleted_count=0, seat_count=1)


class FakeEventRepository:
    """Hands out a FakeAggregate through the same context-manager API."""

    def __init__(self, is_active: bool = False, order_count: int = 0, occupied: int = 0):
        self.calls: list[str] = []
        self.aggregate = FakeAggregate(self.calls, an_event(is_active), order_count, occupied)

    @asynccontextmanager
    async def load(self, event_id: int):
        self.calls.append("load")
        yield self.aggregate

    @asynccontextmanager
    async def load_for_update(self, event_id: int):
        self.calls.append("load_for_update")
        yield self.aggregate

    @asynccontextmanager
    async def create(self, payload):
        self.calls.append("create")
        yield self.aggregate
