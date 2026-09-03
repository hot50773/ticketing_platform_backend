"""EventLayoutService unit tests: ordering guarantees around the layout rules."""
import pytest

from app.exceptions import EventPublishedError, SeatOccupiedError
from app.schemas.event import EventSeatLayout, EventSeatLayoutUpdate, EventZoneCreate
from app.services.event_layout import EventLayoutService
from app.services.mappers import EventSchemaMapper
from app.services.validators import EventRuleValidator
from tests.unit._fakes import FakeEventRepository


def a_service(repository: FakeEventRepository) -> EventLayoutService:
    return EventLayoutService(
        event_repository=repository,
        event_rule_validator=EventRuleValidator(),
        event_schema_mapper=EventSchemaMapper(),
    )


def a_layout() -> EventSeatLayoutUpdate:
    return EventSeatLayoutUpdate(seats=[EventSeatLayout(row=1, col=1)])


def a_zone_payload() -> EventZoneCreate:
    return EventZoneCreate(name="VIP", price=100, seats=[EventSeatLayout(row=1, col=1)])


@pytest.mark.asyncio
async def test_layout_sync_checks_then_writes_in_one_transaction():
    repo = FakeEventRepository()
    result = await a_service(repo).replace_event_zone_seats(1, 10, a_layout())

    assert repo.calls == ["load_for_update", "occupied_seat_count", "sync_zone_seats"]
    assert result.created_count == 1


@pytest.mark.asyncio
async def test_published_event_stops_the_sync_before_any_write():
    repo = FakeEventRepository(is_active=True)

    with pytest.raises(EventPublishedError):
        await a_service(repo).replace_event_zone_seats(1, 10, a_layout())

    assert "sync_zone_seats" not in repo.calls


@pytest.mark.asyncio
async def test_occupied_seat_stops_the_sync_before_any_write():
    repo = FakeEventRepository(occupied=2)

    with pytest.raises(SeatOccupiedError):
        await a_service(repo).replace_event_zone_seats(1, 10, a_layout())

    assert "sync_zone_seats" not in repo.calls


@pytest.mark.asyncio
async def test_published_event_cannot_gain_a_zone():
    repo = FakeEventRepository(is_active=True)

    with pytest.raises(EventPublishedError):
        await a_service(repo).create_event_zone(1, a_zone_payload())

    assert "add_zone" not in repo.calls


@pytest.mark.asyncio
async def test_zone_profile_edit_is_allowed_while_published():
    """name and price stay editable after the event goes live."""
    repo = FakeEventRepository(is_active=True)
    result = await a_service(repo).update_event_zone_profile(1, 10, a_zone_payload())

    assert repo.calls == ["load_for_update", "update_zone_profile"]
    assert result.price == 100


@pytest.mark.asyncio
async def test_seat_listing_does_not_take_the_write_lock():
    from app.schemas import PaginationParams

    repo = FakeEventRepository()
    await a_service(repo).list_event_zone_seats(1, 10, PaginationParams())

    assert repo.calls == ["load", "zone_seats"]
