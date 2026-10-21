"""OrderService unit tests for create_order: no database, a recording fake factory.

These cover what e2e cannot see - that a rejected order never reaches the
insert, and that the amounts written come from the event rather than from the
request.
"""
from datetime import timedelta

import pytest

from app.exceptions import (
    EntityNotFoundError,
    EventNotPublishedError,
    EventStartedError,
    TicketPriceNotReadyError,
)
from app.libs.clock.functional import now_utc
from app.schemas.order import OrderCreate
from app.services.calculators.order import DEFAULT_PAYMENT_WINDOW, OrderCalculator
from app.services.mappers.order import OrderSchemaMapper
from app.services.order import OrderService, UnimplementedPaymentGateway
from app.services.validators.order import OrderValidator
from tests.unit._fakes import (
    FakeEventLayoutCache,
    FakeEventRepository,
    FakeOrderRepository,
    a_seat_price,
)


def a_service(repository: FakeOrderRepository) -> OrderService:
    """The cache fake shares the repository's `calls` list, so the assertions
    below see where the Redis hold lands relative to the insert."""
    return OrderService(
        order_repository=repository,
        event_repository=FakeEventRepository(),
        order_validator=OrderValidator(),
        order_calculator=OrderCalculator(),
        order_schema_mapper=OrderSchemaMapper(),
        payment_gateway=UnimplementedPaymentGateway(),
        event_layout_cache=FakeEventLayoutCache(repository.calls),
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
