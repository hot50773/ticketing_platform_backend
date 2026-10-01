"""EventService unit tests: no database, only a recording fake aggregate.

These cover what e2e cannot see - above all that a rule violation never reaches
the database, and that each use case enters the repository exactly once.
"""
from datetime import timedelta

import pytest
from redis import RedisError

from app.exceptions import (
    ActiveOrdersExistError,
    EventPublishedError,
    InvalidEventTimeError,
    TicketPriceNotReadyError,
)
from app.libs.clock.functional import now_utc
from app.schemas.event import EventCreate, EventHoldingTimeUpdate
from app.services.event import EventService
from app.services.mappers import EventSchemaMapper
from app.services.validators import EventRuleValidator
from tests.unit._fakes import START, FakeEventLayoutCache, FakeEventRepository


def a_service(
    repository: FakeEventRepository, cache: FakeEventLayoutCache | None = None
) -> EventService:
    """The cache shares the repository's `calls` list, so a test can assert on
    where the Redis write lands among the database writes."""
    return EventService(
        event_repository=repository,
        event_rule_validator=EventRuleValidator(),
        event_schema_mapper=EventSchemaMapper(),
        event_layout_cache=cache or FakeEventLayoutCache(repository.calls),
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

    assert repo.calls == [
        "load_for_update", "order_stats", "soft_delete_cascade", "clear_event_layouts",
    ]


@pytest.mark.asyncio
async def test_delete_does_not_write_when_an_order_exists():
    repo = FakeEventRepository(order_count=1)

    with pytest.raises(ActiveOrdersExistError):
        await a_service(repo).delete_event(1)

    assert "soft_delete_cascade" not in repo.calls


@pytest.mark.asyncio
async def test_unpublish_checks_orders_inside_the_locked_transaction():
    repo = FakeEventRepository(is_active=True, order_count=1)

    with pytest.raises(ActiveOrdersExistError):
        await a_service(repo).unpublish_event(1)

    assert repo.calls == ["load_for_update", "order_stats"]


@pytest.mark.asyncio
async def test_publish_checks_prices_not_orders():
    """The two flows share no precondition: publishing never counts orders."""
    repo = FakeEventRepository()
    await a_service(repo).publish_event(1)

    assert repo.calls == ["load_for_update", "event_layout", "init_event_layouts", "publish"]


@pytest.mark.asyncio
async def test_publish_warms_the_bitmap_before_it_writes():
    """The bitmap has to exist before is_active is committed, or the event goes
    live for a moment with no seats in Redis."""
    repo = FakeEventRepository()
    cache = FakeEventLayoutCache(repo.calls)
    await a_service(repo, cache).publish_event(1)

    assert repo.calls.index("init_event_layouts") < repo.calls.index("publish")
    assert [seat.serial_number for seat in cache.warmed[0].zones[0].seats] == [0, 1]


@pytest.mark.asyncio
async def test_publish_does_not_write_when_the_cache_fails():
    """A dead Redis must abort the publish: the transaction unwinds, so the
    event stays unpublished rather than going live without a bitmap."""
    repo = FakeEventRepository()
    cache = FakeEventLayoutCache(repo.calls, fail=True)

    with pytest.raises(RedisError):
        await a_service(repo, cache).publish_event(1)

    assert "publish" not in repo.calls


@pytest.mark.asyncio
async def test_publish_does_not_warm_the_cache_for_an_unpriced_zone():
    """The rule runs before Redis is touched, so a rejected publish leaves no
    orphan bitmap behind."""
    repo = FakeEventRepository(zone_price=None)

    with pytest.raises(TicketPriceNotReadyError):
        await a_service(repo).publish_event(1)

    assert repo.calls == ["load_for_update", "event_layout"]


@pytest.mark.asyncio
async def test_unpublish_checks_orders_not_prices():
    """...and unpublishing never looks at prices, so an unpriced zone that was
    never published can still be taken off sale."""
    repo = FakeEventRepository(is_active=True)
    await a_service(repo).unpublish_event(1)

    assert repo.calls == [
        "load_for_update", "order_stats", "zone_ids", "unpublish", "clear_event_layouts",
    ]


@pytest.mark.asyncio
async def test_unpublish_clears_the_bitmap_only_after_it_writes():
    """The mirror of publish: dropping a live event's bitmap would make every
    seat read as non-existent, so the delete waits until the event is off sale."""
    repo = FakeEventRepository(is_active=True)
    cache = FakeEventLayoutCache(repo.calls)
    await a_service(repo, cache).unpublish_event(1)

    assert repo.calls.index("unpublish") < repo.calls.index("clear_event_layouts")
    assert cache.cleared == [(1, [10])]


@pytest.mark.asyncio
async def test_delete_clears_the_bitmap_only_after_it_writes():
    repo = FakeEventRepository()
    cache = FakeEventLayoutCache(repo.calls)
    await a_service(repo, cache).delete_event(1)

    assert repo.calls.index("soft_delete_cascade") < repo.calls.index("clear_event_layouts")
    assert cache.cleared == [(1, [10])]


@pytest.mark.asyncio
async def test_blocked_unpublish_never_touches_the_cache():
    repo = FakeEventRepository(is_active=True, order_count=1)
    cache = FakeEventLayoutCache(repo.calls)

    with pytest.raises(ActiveOrdersExistError):
        await a_service(repo, cache).unpublish_event(1)

    assert cache.cleared == []


@pytest.mark.asyncio
async def test_reads_do_not_take_the_write_lock():
    repo = FakeEventRepository()
    await a_service(repo).get_event(1)

    assert repo.calls == ["load", "layout"]
