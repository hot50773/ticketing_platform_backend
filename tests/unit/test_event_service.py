"""Service-level unit tests: no database, only a recording fake repository.

These cover the ordering guarantees that e2e tests cannot see - above all
"a rule violation must never reach the database".
"""
from datetime import timedelta

import pytest

from app.exceptions import ActiveOrdersExistError, InvalidEventTimeError
from app.libs.clock.functional import now_utc
from app.models import Event, EventZone
from app.schemas.event import (
    EventCreate,
    EventHoldingTimeUpdate,
    EventLayoutInternal,
    EventOrderStatsInternal,
    EventZoneWithSeatCountInternal,
)
from app.services.event import EventService
from app.services.validators import EventRuleValidator

START = now_utc() + timedelta(days=30)


def an_event(**overrides) -> Event:
    defaults = dict(
        id=1,
        name="World Tour",
        description="d",
        start_at=START,
        end_at=START + timedelta(hours=3),
        is_active=False,
        venue_id=1,
        created_at=START,
        updated_at=START,
    )
    return Event(**{**defaults, **overrides})


class FakeEventRepository:
    """Records every call so the service's ordering can be asserted."""

    def __init__(self, event: Event | None = None, order_count: int = 0):
        self.calls: list[str] = []
        self._event = event or an_event()
        self._order_count = order_count

    async def create_event_from_venue_template(self, payload):
        self.calls.append("create_event_from_venue_template")
        return EventLayoutInternal(
            event=self._event,
            zones=[
                EventZoneWithSeatCountInternal(
                    zone=EventZone(
                        id=10, event_id=1, name="A", price=None,
                        created_at=START, updated_at=START,
                    ),
                    seat_count=4,
                )
            ],
        )

    async def get_and_check_event(self, event_id):
        self.calls.append("get_and_check_event")
        return self._event

    async def get_event_order_stats(self, event_id):
        self.calls.append("get_event_order_stats")
        return EventOrderStatsInternal(active_order_count=self._order_count)

    async def update_event_holding_time(self, event_id, payload):
        self.calls.append("update_event_holding_time")
        return self._event

    async def soft_delete_event_cascade(self, event_id):
        self.calls.append("soft_delete_event_cascade")


def a_service(repository: FakeEventRepository) -> EventService:
    return EventService(event_repository=repository, event_rule_validator=EventRuleValidator())


@pytest.mark.asyncio
async def test_create_event_hits_the_repository_once_validation_passes():
    repo = FakeEventRepository()
    result = await a_service(repo).create_event(
        EventCreate(
            name="World Tour", description="d",
            start_at=START, end_at=START + timedelta(hours=3), venue_id=1,
        )
    )
    assert repo.calls == ["create_event_from_venue_template"]
    assert result.zones[0].seat_count == 4


@pytest.mark.asyncio
async def test_invalid_holding_time_never_reaches_the_repository():
    """The whole point of validating first: no wasted transaction, no partial write."""
    repo = FakeEventRepository()
    past = now_utc() - timedelta(days=1)

    with pytest.raises(InvalidEventTimeError):
        await a_service(repo).create_event(
            EventCreate(
                name="Yesterday", description="d",
                start_at=past, end_at=past + timedelta(hours=3), venue_id=1,
            )
        )
    assert repo.calls == []


@pytest.mark.asyncio
async def test_holding_time_update_checks_publication_before_the_clock():
    """A published event is rejected for being published, not for its dates -
    so the error tells the caller what to actually fix."""
    repo = FakeEventRepository(event=an_event(is_active=True))
    past = now_utc() - timedelta(days=1)

    with pytest.raises(Exception) as exc:
        await a_service(repo).update_event_holding_time(
            1, EventHoldingTimeUpdate(start_at=past, end_at=past + timedelta(hours=3))
        )
    assert "published" in str(exc.value)
    assert repo.calls == ["get_and_check_event"]


@pytest.mark.asyncio
async def test_delete_does_not_touch_the_database_when_an_order_exists():
    repo = FakeEventRepository(order_count=1)

    with pytest.raises(ActiveOrdersExistError):
        await a_service(repo).delete_event(1)

    assert "soft_delete_event_cascade" not in repo.calls
