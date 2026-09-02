"""Business logic for 活動 CRUD.

Talks to the database only through EventRepository, and delegates every domain
rule to EventRuleValidator (no I/O in the rules, so they unit-test standalone).
"""
import logging

from app.repositories.event import EventRepository
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    EventCreate,
    EventDetailResponse,
    EventHoldingTimeUpdate,
    EventProfileUpdate,
    EventResponse,
    EventSeatLayoutSyncResponse,
    EventSeatLayoutUpdate,
    EventSeatResponse,
    EventStatusUpdate,
    EventZoneCreate,
    EventZoneProfileUpdate,
    EventZoneResponse,
)
from app.services.validators import EventRuleValidator

logger = logging.getLogger(__name__)


class EventService:
    """Service layer orchestrating Event / EventZone / EventSeat use cases."""

    def __init__(
        self,
        event_repository: EventRepository,
        event_rule_validator: EventRuleValidator,
    ):
        self._repository = event_repository
        self._validator = event_rule_validator

    # ----------------------------------------------------------------------- #
    # Event
    # ----------------------------------------------------------------------- #
    async def create_event(self, payload: EventCreate) -> EventDetailResponse:
        """flow: validate holding time
                 -> create event + clone venue layout (single transaction)

        The event is always created unpublished with every zone price unset.
        """
        raise NotImplementedError

    async def list_events(self, pagination: PaginationParams) -> Page[EventResponse]:
        """One page of non-deleted events."""
        raise NotImplementedError

    async def get_event(self, event_id: int) -> EventDetailResponse:
        """Returns an event with its zones (seat counts only, not the seats)."""
        raise NotImplementedError

    async def update_event_profile(
        self, event_id: int, payload: EventProfileUpdate
    ) -> EventResponse:
        """Updates name / description. Allowed while the event is published."""
        raise NotImplementedError

    async def update_event_holding_time(
        self, event_id: int, payload: EventHoldingTimeUpdate
    ) -> EventResponse:
        """flow: assert unpublished -> validate start_at / end_at -> update."""
        raise NotImplementedError

    async def update_event_status(
        self, event_id: int, payload: EventStatusUpdate
    ) -> EventResponse:
        """flow: publish   -> assert every zone has a ticket price -> update
                 unpublish -> assert no live order                 -> update
        """
        raise NotImplementedError

    async def delete_event(self, event_id: int) -> None:
        """flow: assert no confirmed order -> soft-delete event + zones + seats."""
        raise NotImplementedError

    # ----------------------------------------------------------------------- #
    # Event layout
    # ----------------------------------------------------------------------- #
    async def create_event_zone(
        self, event_id: int, payload: EventZoneCreate
    ) -> EventZoneResponse:
        """flow: assert unpublished -> create the zone and its seats atomically."""
        raise NotImplementedError

    async def update_event_zone_profile(
        self, event_id: int, event_zone_id: int, payload: EventZoneProfileUpdate
    ) -> EventZoneResponse:
        """Updates a zone's name and ticket price."""
        raise NotImplementedError

    async def list_event_zone_seats(
        self, event_id: int, event_zone_id: int, pagination: PaginationParams
    ) -> Page[EventSeatResponse]:
        """One page of a zone's seat layout with each seat's current status."""
        raise NotImplementedError

    async def replace_event_zone_seats(
        self, event_id: int, event_zone_id: int, payload: EventSeatLayoutUpdate
    ) -> EventSeatLayoutSyncResponse:
        """flow: assert unpublished -> assert no occupied seat -> sync the layout."""
        raise NotImplementedError
