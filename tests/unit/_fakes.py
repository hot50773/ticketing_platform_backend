"""Fake aggregates, repositories and layout cache for service-level unit tests.

All three share one `calls` list, so a test can assert on the exact sequence of
things a use case did - including what it did *not* do after a rule failed, and
how a Redis write interleaves with the database writes around it.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from redis import RedisError

from app.exceptions import MANAGER_NOT_FOUND_MSG, ORDER_NOT_FOUND_MSG, EntityConflictError, EntityNotFoundError
from app.libs.clock.functional import now_utc
from app.models import Customer, Event, EventZone, Order, Manager
from app.schemas import Page
from app.enums import OrderStatus, PaymentStatus, SeatStatus
from app.schemas.event import (
    EventLayoutInternal,
    EventLayoutSummaryInternal,
    EventListCacheFieldInternal,
    EventOrderStatsInternal,
    EventSeatInternal,
    EventSeatSyncCountsInternal,
    EventStateInternal,
    EventZoneWithSeatCountInternal,
    EventSeatsPricingInternal,
    EventZoneWithSeatInternal,
    SeatPositionInternal,
    SeatPriceInternal,
)
from app.schemas.customer import CustomerCreateInternal
from app.schemas.manager import ManagerCreateInternal
from app.schemas.order import (
    OrderCreateInternal,
    OrderStateInternal,
    OrderTicketInternal,
    OrderTicketPricingInternal,
    OrderWithTicketsInternal,
)

START = now_utc() + timedelta(days=30)


def an_event(is_active: bool = False, event_id: int = 1) -> Event:
    return Event(
        id=event_id,
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
        return Page(items=[], total=0, page=pagination.page, page_size=pagination.page_size)

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
        selling_total: int = 0,
        list_error: Exception | None = None,
        missing: bool = False,
    ):
        self.calls: list[str] = []
        self._missing = missing
        self.list_reads: list = []
        self._selling_total = selling_total
        self._list_error = list_error
        self.aggregate = FakeAggregate(
            self.calls, an_event(is_active), order_count, occupied, zone_price
        )

    @asynccontextmanager
    async def load(self, event_id: int):
        self.calls.append("load")
        if self._missing:
            raise EntityNotFoundError(f"Event with id {event_id} not found")
        yield self.aggregate

    @asynccontextmanager
    async def load_for_update(self, event_id: int):
        self.calls.append("load_for_update")
        if self._missing:
            raise EntityNotFoundError(f"Event with id {event_id} not found")
        yield self.aggregate

    @asynccontextmanager
    async def create(self, payload):
        self.calls.append("create")
        yield self.aggregate

    async def get_events(self, pagination, mode) -> Page:
        """Events 1..selling_total are on sale, windowed like the real LIMIT/OFFSET."""
        self.list_reads.append(pagination)
        if self._list_error:
            raise self._list_error
        ids = range(1, self._selling_total + 1)[pagination.offset:pagination.offset + pagination.page_size]
        return Page(
            items=[an_event(is_active=True, event_id=i) for i in ids],
            total=self._selling_total, page=pagination.page, page_size=pagination.page_size,
        )


class FakeBackgroundTasks:
    """Collects what FastAPI would run after the response; a test runs them itself."""

    def __init__(self):
        self.added: list = []

    def add_task(self, func, *args, **kwargs) -> None:
        self.added.append((func, args, kwargs))

    async def run(self) -> None:
        for func, args, kwargs in self.added:
            await func(*args, **kwargs)


class FakeEventListCache:
    """`entry` is what every cached page read returns; None means a miss."""

    def __init__(
        self,
        calls: list[str],
        entry: EventListCacheFieldInternal | None = None,
        lock_free: bool = True,
        refresh_error: Exception | None = None,
    ):
        self.calls = calls
        self._entry = entry
        self._lock_free = lock_free
        self._refresh_error = refresh_error
        self.refreshed: list[Page] = []
        self.lock_held = False

    async def get_paged_events(self, pagination) -> EventListCacheFieldInternal | None:
        return self._entry

    async def refresh_all(self, pages: list[Page]) -> None:
        self.calls.append("refresh_all")
        if self._refresh_error:
            raise self._refresh_error
        self.refreshed = pages

    async def acquire_refresh_lock(self) -> bool:
        self.calls.append("acquire_refresh_lock")
        self.lock_held = self._lock_free
        return self._lock_free

    async def release_refresh_lock(self) -> None:
        self.calls.append("release_refresh_lock")
        self.lock_held = False


class FakeEventDetailCache:
    """`entry` is what a read returns; None means a miss."""

    def __init__(
        self,
        calls: list[str],
        entry=None,
        lock_free: bool = True,
        set_error: Exception | None = None,
    ):
        self.calls = calls
        self._entry = entry
        self._lock_free = lock_free
        self._set_error = set_error
        self.stored: dict = {}
        self.deleted: list[int] = []
        self.lock_held = False

    async def get(self, event_id: int):
        return self._entry

    async def set(self, detail) -> None:
        self.calls.append("set_detail")
        if self._set_error:
            raise self._set_error
        self.stored[detail.id] = detail

    async def delete(self, event_id: int) -> None:
        self.calls.append("delete_detail")
        self.deleted.append(event_id)

    async def sync(self, detail) -> None:
        if detail.is_active:
            await self.set(detail)
        else:
            await self.delete(detail.id)

    async def acquire_refresh_lock(self, event_id: int) -> bool:
        self.calls.append("acquire_detail_lock")
        self.lock_held = self._lock_free
        return self._lock_free

    async def release_refresh_lock(self, event_id: int) -> None:
        self.calls.append("release_detail_lock")
        self.lock_held = False


class FakeEventLayoutCache:
    """Records the Redis warm-up instead of performing it.

    Takes the repository's `calls` list so a test can see where the cache write
    lands relative to the database writes - the ordering is the whole point of
    warming the bitmap before the publish commits.

    `fail=True` makes the write raise, which is how a test pins down that a
    cache failure aborts the publish rather than leaving a live event with no
    bitmap behind it.
    """

    def __init__(
        self,
        calls: list[str],
        fail: bool = False,
        lock_fails: bool = False,
        release_fails: bool = False,
        release_error: Exception | None = None,
    ):
        self.calls = calls
        self._fail = fail
        self._lock_fails = lock_fails or fail
        self._release_error = release_error or (
            RedisError("fake redis is down") if (release_fails or fail) else None
        )
        self.warmed: list[EventLayoutInternal] = []
        self.cleared: list[tuple[int, list[int]]] = []
        self.locked: list[EventSeatsPricingInternal] = []
        self.released: list[tuple[int, list[SeatPositionInternal]]] = []

    async def init_event_layouts(self, event_layout: EventLayoutInternal) -> None:
        self.calls.append("init_event_layouts")
        if self._fail:
            raise RedisError("fake redis is down")
        self.warmed.append(event_layout)

    async def clear_seat_layouts(self, event_id: int, zone_ids: list[int]) -> None:
        self.calls.append("clear_seat_layouts")
        if self._fail:
            raise RedisError("fake redis is down")
        self.cleared.append((event_id, zone_ids))

    async def check_and_lock_seats_atomic(
        self, event_seats_pricing: EventSeatsPricingInternal
    ) -> None:
        self.calls.append("check_and_lock_seats_atomic")
        if self._lock_fails:
            raise RedisError("fake redis is down")
        self.locked.append(event_seats_pricing)

    async def release_seats(
        self, event_id: int, seats: list[SeatPositionInternal]
    ) -> None:
        self.calls.append("release_seats")
        if self._release_error:
            raise self._release_error
        self.released.append((event_id, seats))


# --------------------------------------------------------------------------- #
# Order side
# --------------------------------------------------------------------------- #
def a_seat_price(
    event_seat_id: int,
    price: int | None = 1500,
    event_zone_id: int = 10,
    seat_serial_number: int | None = None,
) -> SeatPriceInternal:
    """One resolved seat at its zone's price.

    serial_number defaults to the seat id: the unit tests never touch a real
    bitmap, so the only property that has to hold is that two seats in a zone
    get two different bit offsets.
    """
    return SeatPriceInternal(
        event_seat_id=event_seat_id,
        event_zone_id=event_zone_id,
        seat_serial_number=(
            seat_serial_number if seat_serial_number is not None else event_seat_id
        ),
        price=price,
    )


def a_seats_pricing(
    seats: list[SeatPriceInternal], event_id: int = 1
) -> EventSeatsPricingInternal:
    """What the factory resolves a cart into.

    Lives here rather than in each test module because the validator, the
    calculator and the service all take this one shape - three private copies
    are what let them drift out of sync with the schema last time.
    """
    return EventSeatsPricingInternal(event_id=event_id, seats=seats)


def an_order(pricing: OrderCreateInternal) -> Order:
    """The row the factory's create() would have written for this pricing."""
    return Order(
        id=1,
        customer_id=pricing.customer_id,
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
    """What place() and load_for_update() hand back: what the mapper and the
    rules read off it, plus the mutations the service asks for."""

    def __init__(self, calls: list[str], pricing: OrderCreateInternal):
        self.calls = calls
        self.pricing = pricing
        self.order = an_order(pricing)
        self.released_seat_ids: list[int] = []

    @property
    def state(self) -> OrderStateInternal:
        return OrderStateInternal(
            id=self.order.id,
            status=self.order.status,
            payment_status=self.order.payment_status,
            payment_deadline=self.order.payment_deadline,
        )

    async def cancel_order(self) -> None:
        self.calls.append("cancel_order")
        self.order.status = OrderStatus.CANCELLED

    async def check_and_release_seats(self) -> None:
        self.calls.append("check_and_release_seats")
        self.released_seat_ids = [t.event_seat_id for t in self.pricing.tickets]

    async def soft_delete_cascade(self, payload, manager_id: int | None) -> None:
        self.calls.append("soft_delete_cascade")
        self.order.deleted_by_manager_id = manager_id
        self.order.deleted_at = now_utc()

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
        seats: list[SeatPriceInternal],
        create_error: Exception | None = None,
    ):
        self.calls = calls
        self._event = event
        self._seats = seats
        self._create_error = create_error
        self.created: OrderCreateInternal | None = None

    @property
    def event_state(self) -> EventStateInternal:
        return EventStateInternal(
            id=self._event.id,
            is_active=self._event.is_active,
            start_at=self._event.start_at,
        )

    async def seats_pricing(self) -> EventSeatsPricingInternal:
        self.calls.append("seats_pricing")
        return a_seats_pricing(self._seats, event_id=self._event.id)

    async def create(self, pricing: OrderCreateInternal) -> FakeOrderAggregate:
        self.calls.append("create")
        if self._create_error:
            raise self._create_error
        self.created = pricing
        return FakeOrderAggregate(self.calls, pricing)


class FakeOrderExpirationScheduler:
    """Records the deadlines handed to the delayed queue instead of enqueuing."""

    def __init__(self):
        self.scheduled: list[tuple[int, datetime]] = []

    async def schedule(self, order_id: int, deadline: datetime) -> None:
        self.scheduled.append((order_id, deadline))


class FakeOrderRepository:
    """Hands out a FakeOrderAggregateFactory through the same context-manager API."""

    def __init__(
        self,
        is_active: bool = True,
        start_at: datetime | None = None,
        seats: list[SeatPriceInternal] | None = None,
        create_error: Exception | None = None,
        status: OrderStatus = OrderStatus.PENDING,
        payment_status: PaymentStatus = PaymentStatus.PENDING,
        payment_deadline: datetime = START,
    ):
        self.calls: list[str] = []
        event = an_event(is_active)
        if start_at is not None:
            event.start_at = start_at
        self.factory = FakeOrderAggregateFactory(
            self.calls,
            event,
            seats if seats is not None else [a_seat_price(1), a_seat_price(2, 800)],
            create_error,
        )

        # The order an existing-order use case (cancel, delete) loads: seats 1
        # and 2 at 1500 each, in whatever state the test asked for.
        self.aggregate = FakeOrderAggregate(
            self.calls,
            OrderCreateInternal(
                customer_id=1,
                comment=None,
                tickets=[
                    OrderTicketPricingInternal(event_seat_id=1, amount=1500),
                    OrderTicketPricingInternal(event_seat_id=2, amount=1500),
                ],
                checkout_amount=3000,
                payment_deadline=payment_deadline,
            ),
        )
        self.aggregate.order.status = status
        self.aggregate.order.payment_status = payment_status

    @asynccontextmanager
    async def load_for_create(self, payload):
        self.calls.append("load_for_create")
        yield self.factory

    @asynccontextmanager
    async def load_for_release(self, order_id: int, customer_id: int | None = None):
        self.calls.append("load_for_release")
        yield self._owned_by(order_id, customer_id)

    @asynccontextmanager
    async def load(self, order_id: int, customer_id: int | None = None):
        self.calls.append("load")
        yield self._owned_by(order_id, customer_id)

    @asynccontextmanager
    async def load_for_update(self, order_id: int, customer_id: int | None = None):
        self.calls.append("load_for_update")
        yield self._owned_by(order_id, customer_id)

    def _owned_by(self, order_id: int, customer_id: int | None) -> FakeOrderAggregate:
        """Mirrors the repository's WHERE customer_id = ..."""
        if customer_id is not None and self.aggregate.order.customer_id != customer_id:
            raise EntityNotFoundError(ORDER_NOT_FOUND_MSG.format(order_id=order_id))
        return self.aggregate


class _FakeAccountTable:
    """An in-memory table keyed by id, shared by the customer and manager fakes."""

    model: type

    def __init__(self):
        self.rows: dict[int, object] = {}

    def add(self, **fields):
        row = self.model(id=len(self.rows) + 1, created_at=START, updated_at=START, **fields)
        self.rows[row.id] = row
        return row

    async def _create(self, data):
        if await self.get_by_email(data.email):
            raise EntityConflictError(data.email)
        return self.add(**data.model_dump())

    async def get_by_email(self, email: str):
        return next((row for row in self.rows.values() if row.email == email), None)


class FakeCustomerRepository(_FakeAccountTable):
    model = Customer

    async def get_or_create_by_google_sub(self, data: CustomerCreateInternal) -> Customer:
        return await self.get_by_google_sub(data.google_sub) or await self._create(data)

    async def get_by_google_sub(self, google_sub: str) -> Customer | None:
        return next((row for row in self.rows.values() if row.google_sub == google_sub), None)

    async def link_google_sub(self, customer_id: int, google_sub: str) -> Customer:
        if await self.get_by_google_sub(google_sub):
            raise EntityConflictError(google_sub)
        customer = self.rows[customer_id]
        customer.google_sub = google_sub
        return customer


class FakeManagerRepository(_FakeAccountTable):
    model = Manager

    async def create_manager(self, data: ManagerCreateInternal) -> Manager:
        return await self._create(data)

    async def update_password(self, manager_id: int, hashed_password: str) -> None:
        self.rows[manager_id].hashed_password = hashed_password

    async def get_manager(self, manager_id: int) -> Manager:
        if manager_id not in self.rows:
            raise EntityNotFoundError(MANAGER_NOT_FOUND_MSG.format(manager_id=manager_id))
        return self.rows[manager_id]

    async def set_active_if_role(self, manager_id: int, role, is_active: bool) -> Manager | None:
        manager = self.rows.get(manager_id)
        if manager is not None and manager.role == role:
            manager.is_active = is_active
        else:
            manager = None
        return manager


class FakeGoogleIdTokenVerifier:
    """Accepts any token and hands back `identity`; `error` makes it reject."""

    def __init__(self, identity=None, error: Exception | None = None):
        self._identity = identity
        self._error = error

    async def verify(self, token: str):
        if self._error:
            raise self._error
        return self._identity


class FakeLoginAttemptLimiter:
    """LoginAttemptLimiter without Redis: a counter per key, no window."""

    def __init__(self, max_failures: int = 5):
        self.max_failures = max_failures
        self.failures: dict[tuple[str, str], int] = {}
        self.resets = 0

    async def failure_count(self, client_ip: str, email: str) -> int:
        return self.failures.get((client_ip, email), 0)

    def is_over_limit(self, failures: int) -> bool:
        return failures >= self.max_failures

    async def record_failure(self, client_ip: str, email: str) -> None:
        key = (client_ip, email)
        self.failures[key] = self.failures.get(key, 0) + 1

    async def reset(self, client_ip: str, email: str) -> None:
        self.resets += 1
        self.failures.pop((client_ip, email), None)
