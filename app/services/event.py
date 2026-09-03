"""Business logic for 活動 CRUD.

Talks to the database only through EventRepository, and delegates every domain
rule to EventRuleValidator (no I/O in the rules, so they unit-test standalone).

The Redis seat bitmap is deliberately absent: nothing here touches a cache.
Warming the bitmap belongs to update_event_status(publish), which is the point
the layout freezes and serial_number becomes a stable offset.
"""
import logging

from app.models import Event, EventSeat, EventZone
from app.repositories.event import EventRepository
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    EventCreate,
    EventDetailResponse,
    EventHoldingTimeUpdate,
    EventLayoutInternal,
    EventProfileUpdate,
    EventResponse,
    EventSeatLayoutSyncResponse,
    EventSeatLayoutUpdate,
    EventSeatResponse,
    EventStatusInternal,
    EventStatusUpdate,
    EventZoneCreate,
    EventZoneProfileUpdate,
    EventZoneResponse,
    EventZoneWithSeatCountInternal,
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
        self._validator.validate_holding_time(payload)
        layout = await self._repository.create_event_from_venue_template(payload)
        return self._detail_response(layout)

    async def list_events(self, pagination: PaginationParams) -> Page[EventResponse]:
        """One page of non-deleted events."""
        page = await self._repository.get_events(pagination)
        return Page[EventResponse](
            items=[self._event_response(event) for event in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )

    async def get_event(self, event_id: int) -> EventDetailResponse:
        """Returns an event with its zones (seat counts only, not the seats)."""
        layout = await self._repository.get_event_layout(event_id)
        return self._detail_response(layout)

    async def update_event_profile(
        self, event_id: int, payload: EventProfileUpdate
    ) -> EventResponse:
        """Updates name / description. Allowed while the event is published."""
        event = await self._repository.update_event_profile(event_id, payload)
        return self._event_response(event)

    async def update_event_holding_time(
        self, event_id: int, payload: EventHoldingTimeUpdate
    ) -> EventResponse:
        """flow: assert unpublished -> validate start_at / end_at -> update."""
        event = await self._repository.get_and_check_event(event_id)
        self._validator.validate_unpublished(self._status_of(event))
        self._validator.validate_holding_time(payload)
        updated = await self._repository.update_event_holding_time(event_id, payload)
        return self._event_response(updated)

    async def update_event_status(
        self, event_id: int, payload: EventStatusUpdate
    ) -> EventResponse:
        """flow: publish   -> assert every zone has a ticket price -> update
                 unpublish -> assert no live order                 -> update
        """
        await self._repository.get_and_check_event(event_id)

        if payload.is_active:
            pricings = await self._repository.get_event_zone_pricings(event_id)
            self._validator.validate_publishable(pricings)
        else:
            order_stats = await self._repository.get_event_order_stats(event_id)
            self._validator.validate_no_live_order(order_stats)

        updated = await self._repository.update_event_status(event_id, payload.is_active)
        return self._event_response(updated)

    async def delete_event(self, event_id: int) -> None:
        """flow: assert no live order -> soft-delete event + zones + seats."""
        await self._repository.get_and_check_event(event_id)
        order_stats = await self._repository.get_event_order_stats(event_id)
        self._validator.validate_no_live_order(order_stats)
        await self._repository.soft_delete_event_cascade(event_id)

    # ----------------------------------------------------------------------- #
    # Event layout
    # ----------------------------------------------------------------------- #
    async def create_event_zone(
        self, event_id: int, payload: EventZoneCreate
    ) -> EventZoneResponse:
        """flow: assert unpublished -> create the zone and its seats atomically."""
        event = await self._repository.get_and_check_event(event_id)
        self._validator.validate_unpublished(self._status_of(event))
        created = await self._repository.create_event_zone_with_seats(event_id, payload)
        return self._zone_response(created)

    async def update_event_zone_profile(
        self, event_id: int, event_zone_id: int, payload: EventZoneProfileUpdate
    ) -> EventZoneResponse:
        """Updates a zone's name and ticket price.

        Deliberately has no unpublished check: name and price stay editable
        after the event goes live.
        """
        await self._repository.get_and_check_event_zone(event_id, event_zone_id)
        updated = await self._repository.update_event_zone_profile(event_zone_id, payload)
        return self._zone_response(updated)

    async def list_event_zone_seats(
        self, event_id: int, event_zone_id: int, pagination: PaginationParams
    ) -> Page[EventSeatResponse]:
        """One page of a zone's seat layout with each seat's current status."""
        await self._repository.get_and_check_event_zone(event_id, event_zone_id)
        page = await self._repository.get_event_zone_seats(event_zone_id, pagination)
        return Page[EventSeatResponse](
            items=[self._seat_response(seat) for seat in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )

    async def replace_event_zone_seats(
        self, event_id: int, event_zone_id: int, payload: EventSeatLayoutUpdate
    ) -> EventSeatLayoutSyncResponse:
        """flow: assert unpublished -> assert no occupied seat -> sync the layout."""
        event = await self._repository.get_and_check_event(event_id)
        self._validator.validate_unpublished(self._status_of(event))
        await self._repository.get_and_check_event_zone(event_id, event_zone_id)

        occupied = await self._repository.count_occupied_seats_in_zone(event_zone_id)
        self._validator.validate_no_occupied_seat(occupied)

        counts = await self._repository.replace_event_zone_seats(event_zone_id, payload.seats)
        return EventSeatLayoutSyncResponse(
            created_count=counts.created_count,
            deleted_count=counts.deleted_count,
            seat_count=counts.seat_count,
        )

    # ----------------------------------------------------------------------- #
    # Mapping (ORM -> API schema); written out so the contract stays visible
    # ----------------------------------------------------------------------- #
    @staticmethod
    def _status_of(event: Event) -> EventStatusInternal:
        return EventStatusInternal(id=event.id, is_active=event.is_active)

    @staticmethod
    def _event_response(event: Event) -> EventResponse:
        return EventResponse(
            id=event.id,
            created_at=event.created_at,
            updated_at=event.updated_at,
            name=event.name,
            description=event.description,
            start_at=event.start_at,
            end_at=event.end_at,
            venue_id=event.venue_id,
            is_active=event.is_active,
        )

    @staticmethod
    def _zone_response(zone_with_count: EventZoneWithSeatCountInternal) -> EventZoneResponse:
        zone: EventZone = zone_with_count.zone
        return EventZoneResponse(
            id=zone.id,
            created_at=zone.created_at,
            updated_at=zone.updated_at,
            event_id=zone.event_id,
            name=zone.name,
            price=zone.price,
            seat_count=zone_with_count.seat_count,
        )

    @staticmethod
    def _seat_response(seat: EventSeat) -> EventSeatResponse:
        return EventSeatResponse(
            id=seat.id,
            created_at=seat.created_at,
            updated_at=seat.updated_at,
            event_zone_id=seat.event_zone_id,
            serial_number=seat.serial_number,
            row=seat.row,
            col=seat.col,
            status=seat.status,
        )

    @classmethod
    def _detail_response(cls, layout: EventLayoutInternal) -> EventDetailResponse:
        event = cls._event_response(layout.event)
        return EventDetailResponse(
            **event.model_dump(),
            zones=[cls._zone_response(zone) for zone in layout.zones],
        )
