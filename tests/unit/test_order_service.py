"""OrderService unit tests for create_order and cancel_order: no database, a recording fake factory.

These cover what e2e cannot see - that a rejected order never reaches the
insert, and that the amounts written come from the event rather than from the
request.
"""
import asyncio
import logging
from datetime import timedelta

import pytest

from app.exceptions import (
    EntityNotFoundError,
    EventNotPublishedError,
    EventStartedError,
    OrderNotCancellableError,
    TicketPriceNotReadyError,
)
from app.enums import OrderStatus, PaymentStatus
from app.libs.clock.functional import now_utc
from app.schemas.order import OrderCreate
from app.services.calculators.order import DEFAULT_PAYMENT_WINDOW, OrderCalculator
from app.services.mappers.order import OrderSchemaMapper
from app.services.order import OrderService, UnimplementedPaymentGateway
from app.services.validators.order import OrderValidator
from redis import RedisError

from tests.unit._fakes import (
    FakeEventLayoutCache,
    FakeOrderExpirationScheduler,
    FakeOrderRepository,
    a_seat_price,
)


def a_service(repository: FakeOrderRepository) -> OrderService:
    """The cache fake shares the repository's `calls` list, so the assertions
    below see where the Redis hold lands relative to the insert."""
    return OrderService(
        order_repository=repository,
        order_validator=OrderValidator(),
        order_calculator=OrderCalculator(),
        order_schema_mapper=OrderSchemaMapper(),
        payment_gateway=UnimplementedPaymentGateway(),
        event_layout_cache=FakeEventLayoutCache(repository.calls),
        order_expiration_scheduler=FakeOrderExpirationScheduler(),
    )


def a_payload(event_seat_ids: list[int] | None = None) -> OrderCreate:
    return OrderCreate(
        user_id=1, event_id=1, event_seat_ids=event_seat_ids or [1, 2]
    )


@pytest.mark.asyncio
async def test_create_order_opens_one_transaction():
    repo = FakeOrderRepository()
    result = await a_service(repo).create_order(a_payload())

    assert repo.calls == [
        "load_for_create",
        "seats_pricing",
        "check_and_lock_seats_atomic",
        "create",
        "with_tickets",
    ]
    assert [ticket.event_seat_id for ticket in result.tickets] == [1, 2]


@pytest.mark.asyncio
async def test_prices_come_from_the_event_not_the_request():
    """The payload carries no amount at all - 1500 + 800 is what the zones say."""
    repo = FakeOrderRepository(seats=[a_seat_price(1, 1500), a_seat_price(2, 800)])

    result = await a_service(repo).create_order(a_payload())

    assert result.checkout_amount == 2300
    assert [ticket.amount for ticket in result.tickets] == [1500, 800]


@pytest.mark.asyncio
async def test_payment_deadline_is_set_by_the_backend():
    repo = FakeOrderRepository()
    before = now_utc()

    result = await a_service(repo).create_order(a_payload())

    assert before + DEFAULT_PAYMENT_WINDOW <= result.payment_deadline
    assert result.payment_deadline <= now_utc() + DEFAULT_PAYMENT_WINDOW


@pytest.mark.asyncio
async def test_unpublished_event_is_rejected_before_any_seat_is_read():
    """The event rule is cheap and the seat query is not, so the order matters."""
    repo = FakeOrderRepository(is_active=False)

    with pytest.raises(EventNotPublishedError):
        await a_service(repo).create_order(a_payload())

    assert repo.calls == ["load_for_create"]


@pytest.mark.asyncio
async def test_started_event_never_reaches_the_insert():
    repo = FakeOrderRepository(start_at=now_utc() - timedelta(seconds=1))

    with pytest.raises(EventStartedError):
        await a_service(repo).create_order(a_payload())

    assert "create" not in repo.calls


@pytest.mark.asyncio
async def test_seat_from_another_event_never_reaches_the_insert():
    """The factory only resolves seats belonging to this event, so seat 2
    coming back unresolved is how "not yours" arrives here."""
    repo = FakeOrderRepository(seats=[a_seat_price(1)])

    with pytest.raises(EntityNotFoundError):
        await a_service(repo).create_order(a_payload([1, 2]))

    assert repo.calls == ["load_for_create", "seats_pricing"]


@pytest.mark.asyncio
async def test_unpriced_zone_never_reaches_the_insert():
    repo = FakeOrderRepository(seats=[a_seat_price(1, price=None)])

    with pytest.raises(TicketPriceNotReadyError):
        await a_service(repo).create_order(a_payload([1]))

    assert repo.calls == ["load_for_create", "seats_pricing"]


# --------------------------------------------------------------------------- #
# The hold outlives its transaction
#
# check_and_lock_seats_atomic writes to Redis, which no database rollback can
# undo. If the insert then fails, the seats stay held by an order that does not
# exist and nobody can buy them. So the service hands them back itself.
# --------------------------------------------------------------------------- #
def a_service_with_cache(
    repository: FakeOrderRepository, cache: FakeEventLayoutCache
) -> OrderService:
    return OrderService(
        order_repository=repository,
        order_validator=OrderValidator(),
        order_calculator=OrderCalculator(),
        order_schema_mapper=OrderSchemaMapper(),
        payment_gateway=UnimplementedPaymentGateway(),
        event_layout_cache=cache,
        order_expiration_scheduler=FakeOrderExpirationScheduler(),
    )


@pytest.mark.asyncio
async def test_a_lost_race_hands_nothing_back():
    """The hold belongs to whoever won it - releasing here would hand them
    somebody else's seats."""
    repo = FakeOrderRepository()
    cache = FakeEventLayoutCache(repo.calls, lock_fails=True)

    with pytest.raises(RedisError):
        await a_service_with_cache(repo, cache).create_order(a_payload())

    assert "release_seats" not in repo.calls


@pytest.mark.asyncio
async def test_a_failed_insert_hands_the_seats_back():
    repo = FakeOrderRepository(create_error=RuntimeError("insert blew up"))
    cache = FakeEventLayoutCache(repo.calls)

    with pytest.raises(RuntimeError, match="insert blew up"):
        await a_service_with_cache(repo, cache).create_order(a_payload())

    assert repo.calls == [
        "load_for_create",
        "seats_pricing",
        "check_and_lock_seats_atomic",
        "create",
        "release_seats",
    ]


@pytest.mark.asyncio
async def test_the_seats_handed_back_are_the_ones_that_were_held():
    """Guards the field rename: SeatPriceInternal calls it seat_serial_number,
    SeatPositionInternal calls it serial_number."""
    seats = [a_seat_price(1, event_zone_id=10), a_seat_price(2, event_zone_id=11)]
    repo = FakeOrderRepository(seats=seats, create_error=RuntimeError("boom"))
    cache = FakeEventLayoutCache(repo.calls)

    with pytest.raises(RuntimeError):
        await a_service_with_cache(repo, cache).create_order(a_payload())

    (event_id, handed_back), = cache.released
    assert event_id == 1
    assert [(s.event_zone_id, s.serial_number) for s in handed_back] == [(10, 1), (11, 2)]


@pytest.mark.asyncio
async def test_a_failed_handback_does_not_replace_the_real_error():
    """Whatever broke the insert is what the caller has to debug; a Redis
    failure on the way out must not take its place."""
    repo = FakeOrderRepository(create_error=RuntimeError("insert blew up"))
    cache = FakeEventLayoutCache(repo.calls, release_fails=True)

    with pytest.raises(RuntimeError, match="insert blew up"):
        await a_service_with_cache(repo, cache).create_order(a_payload())


@pytest.mark.asyncio
async def test_a_successful_order_hands_nothing_back():
    repo = FakeOrderRepository()
    cache = FakeEventLayoutCache(repo.calls)

    await a_service_with_cache(repo, cache).create_order(a_payload())

    assert "release_seats" not in repo.calls


@pytest.mark.asyncio
async def test_a_broken_unlock_is_not_swallowed():
    """Only a Redis outage is tolerated. A defect in our own unlock code has to
    surface rather than quietly leave seats locked on every failed order."""
    repo = FakeOrderRepository(create_error=RuntimeError("insert blew up"))
    cache = FakeEventLayoutCache(repo.calls, release_error=TypeError("bug on the way out"))

    with pytest.raises(TypeError, match="bug on the way out") as exc:
        await a_service_with_cache(repo, cache).create_order(a_payload())

    # Demoted, not lost: the failure that sent us here is still the context.
    assert isinstance(exc.value.__context__, RuntimeError)


@pytest.mark.asyncio
async def test_the_log_names_the_seats_left_locked(caplog):
    """No order exists for these seats, so this log line is the only thing that
    says which ones are stuck."""
    seats = [a_seat_price(1, event_zone_id=10), a_seat_price(2, event_zone_id=11)]
    repo = FakeOrderRepository(seats=seats, create_error=RuntimeError("boom"))
    cache = FakeEventLayoutCache(repo.calls, release_fails=True)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        await a_service_with_cache(repo, cache).create_order(a_payload())

    assert "(10, 1)" in caplog.text and "(11, 2)" in caplog.text


@pytest.mark.asyncio
async def test_being_cancelled_mid_unlock_is_not_swallowed():
    """A cancelled request still has to stop. Catching BaseException here would
    log the cancellation as a bug of ours and let the task carry on."""
    repo = FakeOrderRepository(create_error=RuntimeError("insert blew up"))
    cache = FakeEventLayoutCache(repo.calls, release_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await a_service_with_cache(repo, cache).create_order(a_payload())


# --------------------------------------------------------------------------- #
# Releasing an order: cancel, delete, expiry
#
# The seats go back in Postgres inside the order's own transaction, so the
# state change and the release commit together; Redis is cleared afterwards.
# --------------------------------------------------------------------------- #
def a_release_setup(
    repository: FakeOrderRepository,
) -> tuple[OrderService, FakeEventLayoutCache]:
    cache = FakeEventLayoutCache(repository.calls)
    service = OrderService(
        order_repository=repository,
        order_validator=OrderValidator(),
        order_calculator=OrderCalculator(),
        order_schema_mapper=OrderSchemaMapper(),
        payment_gateway=UnimplementedPaymentGateway(),
        event_layout_cache=cache,
        order_expiration_scheduler=FakeOrderExpirationScheduler(),
    )
    return service, cache


@pytest.mark.asyncio
async def test_cancel_releases_the_seats_in_the_same_transaction():
    repo = FakeOrderRepository()
    service, _ = a_release_setup(repo)

    result = await service.cancel_order(1)

    assert result.status == OrderStatus.CANCELLED
    assert repo.calls == [
        "load_for_release",
        "with_tickets",
        "check_and_release_seats",
        "cancel_order",
        "release_seats",
    ]


@pytest.mark.asyncio
async def test_cancel_releases_the_seats_of_this_order():
    repo = FakeOrderRepository()
    service, cache = a_release_setup(repo)

    await service.cancel_order(1)

    assert repo.aggregate.released_seat_ids == [1, 2]
    (event_id, handed_back), = cache.released
    assert event_id == 1
    assert [(s.event_zone_id, s.serial_number) for s in handed_back] == [(10, 0), (10, 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payment_status"),
    [
        (OrderStatus.CANCELLED, PaymentStatus.PENDING),
        (OrderStatus.CONFIRMED, PaymentStatus.SUCCESS),
        (OrderStatus.PENDING, PaymentStatus.IN_PROGRESS),
    ],
)
async def test_an_uncancellable_order_never_touches_the_seats(status, payment_status):
    repo = FakeOrderRepository(status=status, payment_status=payment_status)
    service, _ = a_release_setup(repo)

    with pytest.raises(OrderNotCancellableError):
        await service.cancel_order(1)

    assert repo.calls == ["load_for_release"]


@pytest.mark.asyncio
async def test_an_expired_order_is_released_before_it_is_soft_deleted():
    """soft_delete_cascade hides the tickets the release reads."""
    repo = FakeOrderRepository(payment_deadline=now_utc() - timedelta(seconds=1))
    service, _ = a_release_setup(repo)

    await service.check_order_expiration(1)

    assert repo.calls == [
        "load_for_release",
        "with_tickets",
        "check_and_release_seats",
        "soft_delete_cascade",
        "release_seats",
    ]


@pytest.mark.asyncio
async def test_an_order_still_within_its_deadline_is_left_alone():
    repo = FakeOrderRepository()
    service, _ = a_release_setup(repo)

    await service.check_order_expiration(1)

    assert repo.calls == ["load_for_release"]
