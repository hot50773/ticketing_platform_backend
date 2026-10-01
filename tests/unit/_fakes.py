"""Fake aggregates, repositories and layout cache for service-level unit tests.

All three share one `calls` list, so a test can assert on the exact sequence of
things a use case did - including what it did *not* do after a rule failed, and
how a Redis write interleaves with the database writes around it.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from redis import RedisError

from app.libs.clock.functional import now_utc
from app.models import Event, EventZone, Order
from app.schemas import Page
from app.enums import OrderStatus, PaymentStatus, SeatStatus
from app.schemas.event import (
    EventLayoutInternal,
    EventLayoutSummaryInternal,
    EventOrderStatsInternal,
    EventSeatInternal,
    EventSeatSyncCountsInternal,
    EventStateInternal,
    EventZoneWithSeatCountInternal,
    EventSeatPriceInternal,
    EventZoneWithSeatInternal,
)
from app.schemas.order import (
    OrderPricingInternal,
    OrderTicketInternal,
    OrderWithTicketsInternal,
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


def a_zone_with_seats(name: str = "A", price: int | None = 1500) -> EventZoneWithSeatInternal:
    """A priced zone with two seats, so publish_event has a layout to warm."""
    return EventZoneWithSeatInternal(
        id=10,
        name=name,
        price=price,
        seats=[
            EventSeatInternal(id=100, serial_number=0, row=1, col=1, status=SeatStatus.AVAILABLE),
            EventSeatInternal(id=101, serial_number=1, row=1, col=2, status=SeatStatus.AVAILABLE),
        ],
    )


class FakeAggregate:
    def __init__(
        self,
        calls: list[str],
        event: Event,
        order_count: int,
        occupied: int,
        zone_price: int | None = 1500,
    ):
        self.calls = calls
        self._event = event
        self._order_count = order_count
        self._occupied = occupied
        self._zone_price = zone_price

    @property
    def event(self) -> Event:
        return self._event

    @property
    def state(self) -> EventStateInternal:
        return EventStateInternal(
            id=self._event.id,
            is_active=self._event.is_active,
            start_at=self._event.start_at,
        )

    async def layout(self) -> EventLayoutSummaryInternal:
        self.calls.append("layout")
        return EventLayoutSummaryInternal(
            event=self._event,
            zones=[EventZoneWithSeatCountInternal(zone=a_zone(), seat_count=4)],
        )

    async def event_layout(self) -> EventLayoutInternal:
        """The full layout, seats included - what publish_event hands the cache."""
        self.calls.append("event_layout")
        return EventLayoutInternal(
            event=self._event, zones=[a_zone_with_seats(price=self._zone_price)]
        )

    async def zone_ids(self) -> list[int]:
        self.calls.append("zone_ids")
        return [10]

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

    async def publish(self) -> None:
        self.calls.append("publish")

    async def unpublish(self) -> None:
        self.calls.append("unpublish")

    async def soft_delete_cascade(self) -> list[int]:
        self.calls.append("soft_delete_cascade")
        return [10]

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

    def __init__(
        self,
        is_active: bool = False,
        order_count: int = 0,
        occupied: int = 0,
        zone_price: int | None = 1500,
    ):
        self.calls: list[str] = []
        self.aggregate = FakeAggregate(
            self.calls, an_event(is_active), order_count, occupied, zone_price
        )

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


class FakeEventLayoutCache:
    """Records the Redis warm-up instead of performing it.

    Takes the repository's `calls` list so a test can see where the cache write
    lands relative to the database writes - the ordering is the whole point of
    warming the bitmap before the publish commits.

    `fail=True` makes the write raise, which is how a test pins down that a
    cache failure aborts the publish rather than leaving a live event with no
    bitmap behind it.
    """

    def __init__(self, calls: list[str], fail: bool = False):
        self.calls = calls
        self._fail = fail
        self.warmed: list[EventLayoutInternal] = []
        self.cleared: list[tuple[int, list[int]]] = []

    async def init_event_layouts(self, event_layout: EventLayoutInternal) -> None:
        self.calls.append("init_event_layouts")
        if self._fail:
            raise RedisError("fake redis is down")
        self.warmed.append(event_layout)

    async def clear_event_layouts(self, event_id: int, zone_ids: list[int]) -> None:
        self.calls.append("clear_event_layouts")
        if self._fail:
            raise RedisError("fake redis is down")
        self.cleared.append((event_id, zone_ids))


# --------------------------------------------------------------------------- #
# Order side
# --------------------------------------------------------------------------- #
def a_seat_price(
    event_seat_id: int, price: int | None = 1500, event_zone_id: int = 10
) -> EventSeatPriceInternal:
    return EventSeatPriceInternal(
        event_seat_id=event_seat_id, event_zone_id=event_zone_id, price=price
    )


def an_order(pricing: OrderPricingInternal) -> Order:
    """The row the factory's create() would have written for this pricing."""
    return Order(
        id=1,
        user_id=1,
        event_id=1,
        checkout_amount=pricing.checkout_amount,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        payment_deadline=pricing.payment_deadline,
        comment=None,
        created_at=START,
        updated_at=START,
        deleted_at=None,
    )


class FakeOrderAggregate:
    """What place() hands back: only what the mapper reads off it."""

    def __init__(self, calls: list[str], pricing: OrderPricingInternal):
        self.calls = calls
        self.pricing = pricing
        self.order = an_order(pricing)

    def with_tickets(self) -> OrderWithTicketsInternal:
        self.calls.append("with_tickets")
        return OrderWithTicketsInternal(
            order=self.order,
            tickets=[
                OrderTicketInternal(
                    id=index,
                    created_at=START,
                    updated_at=START,
                    event_seat_id=ticket.event_seat_id,
                    event_zone_id=10,
                    zone_name="A",
                    row=1,
                    col=index,
                    serial_number=index,
                    amount=ticket.amount,
                )
                for index, ticket in enumerate(self.pricing.tickets)
            ],
        )


class FakeOrderAggregateFactory:
    """Stands in for the real factory: the pre-insert half of the create flow.

    `seats` is what the database would have resolved, so a test can drop a seat
    from it to say "this seat does not belong to the event" without needing one.
    """

    def __init__(
        self,
        calls: list[str],
        event: Event,
        seats: list[EventSeatPriceInternal],
    ):
        self.calls = calls
        self._event = event
        self._seats = seats
        self.created: OrderPricingInternal | None = None

    @property
    def event_state(self) -> EventStateInternal:
        return EventStateInternal(
            id=self._event.id,
            is_active=self._event.is_active,
            start_at=self._event.start_at,
        )

    async def seat_prices(self) -> list[EventSeatPriceInternal]:
        self.calls.append("seat_prices")
        return self._seats

    async def create(self, pricing: OrderPricingInternal) -> FakeOrderAggregate:
        self.calls.append("create")
        self.created = pricing
        return FakeOrderAggregate(self.calls, pricing)


class FakeOrderRepository:
    """Hands out a FakeOrderAggregateFactory through the same context-manager API."""

    def __init__(
        self,
        is_active: bool = True,
        start_at: datetime | None = None,
        seats: list[EventSeatPriceInternal] | None = None,
    ):
        self.calls: list[str] = []
        event = an_event(is_active)
        if start_at is not None:
            event.start_at = start_at
        self.factory = FakeOrderAggregateFactory(
            self.calls,
            event,
            seats if seats is not None else [a_seat_price(1), a_seat_price(2, 800)],
        )

    @asynccontextmanager
    async def load_for_create(self, payload):
        self.calls.append("load_for_create")
        yield self.factory
