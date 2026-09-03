"""EventService unit tests: no database, only a recording fake aggregate.

These cover what e2e cannot see - above all that a rule violation never reaches
the database, and that each use case enters the repository exactly once.
"""
from datetime import timedelta

import pytest

from app.exceptions import ActiveOrdersExistError, EventPublishedError, InvalidEventTimeError
from app.libs.clock.functional import now_utc
from app.schemas.event import EventCreate, EventHoldingTimeUpdate, EventStatusUpdate
from app.services.event import EventService
from app.services.mappers import EventSchemaMapper
from app.services.validators import EventRuleValidator
from tests.unit._fakes import START, FakeEventRepository


def a_service(repository: FakeEventRepository) -> EventService:
    return EventService(
        event_repository=repository,
        event_rule_validator=EventRuleValidator(),
        event_schema_mapper=EventSchemaMapper(),
    )


def a_payload(start_at=None) -> EventCreate:
    start_at = start_at or START
    return EventCreate(
        name="World Tour", description="d",
        start_at=start_at, end_at=start_at + timedelta(hours=3), venue_id=1,
    )


@pytest.mark.asyncio
async def test_create_event_opens_one_transaction():
    repo = FakeEventRepository()
    result = await a_service(repo).create_event(a_payload())

    assert repo.calls == ["create", "layout"]
    assert result.zones[0].seat_count == 4


@pytest.mark.asyncio
async def test_invalid_holding_time_never_opens_a_transaction():
    """Validating first means no wasted transaction and no partial write."""
    repo = FakeEventRepository()
    past = now_utc() - timedelta(days=1)

    with pytest.raises(InvalidEventTimeError):
        await a_service(repo).create_event(a_payload(start_at=past))

    assert repo.calls == []


@pytest.mark.asyncio
async def test_holding_time_update_checks_publication_before_the_clock():
    """A published event is rejected for being published, not for its dates -
    so the error names what the caller can actually fix."""
    repo = FakeEventRepository(is_active=True)
    past = now_utc() - timedelta(days=1)

    with pytest.raises(EventPublishedError):
        await a_service(repo).update_event_holding_time(
            1, EventHoldingTimeUpdate(start_at=past, end_at=past + timedelta(hours=3))
        )

    assert repo.calls == ["load_for_update"]


@pytest.mark.asyncio
async def test_delete_reads_and_writes_in_the_same_transaction():
    """One entry point: the order count and the delete cannot drift apart."""
    repo = FakeEventRepository()
    await a_service(repo).delete_event(1)

    assert repo.calls == ["load_for_update", "order_stats", "soft_delete"]


@pytest.mark.asyncio
async def test_delete_does_not_write_when_an_order_exists():
    repo = FakeEventRepository(order_count=1)

    with pytest.raises(ActiveOrdersExistError):
        await a_service(repo).delete_event(1)

    assert "soft_delete" not in repo.calls


@pytest.mark.asyncio
async def test_unpublish_checks_orders_inside_the_locked_transaction():
    repo = FakeEventRepository(is_active=True, order_count=1)

    with pytest.raises(ActiveOrdersExistError):
        await a_service(repo).update_event_status(1, EventStatusUpdate(is_active=False))

    assert repo.calls == ["load_for_update", "order_stats"]


@pytest.mark.asyncio
async def test_publish_checks_prices_not_orders():
    repo = FakeEventRepository()
    await a_service(repo).update_event_status(1, EventStatusUpdate(is_active=True))

    assert repo.calls == ["load_for_update", "zone_pricings", "set_status"]


@pytest.mark.asyncio
async def test_reads_do_not_take_the_write_lock():
    repo = FakeEventRepository()
    await a_service(repo).get_event(1)

    assert repo.calls == ["load", "layout"]
