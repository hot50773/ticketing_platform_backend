"""Translation between the Event aggregate's ORM rows and its API schemas.

Kept out of the services so they read as pure use-case orchestration: the
services decide *what* happens, this decides *what it looks like* on the way
out. Rule inputs are not here - the aggregate exposes those itself. Every field is written out rather than auto-copied, so the API contract
stays greppable and an accidental model change cannot silently leak a column.
"""
from app.models import Event, EventSeat, EventZone
from app.schemas import Page
from app.schemas.event import (
    EventDetailResponse,
    EventLayoutSummaryInternal,
    EventResponse,
    EventSeatLayoutSyncResponse,
    EventSeatResponse,
    EventSeatSyncCountsInternal,
    EventZoneResponse,
    EventZoneWithSeatCountInternal,
)


class EventSchemaMapper:
    """Maps Event / EventZone / EventSeat rows to API schemas and rule inputs."""

    def to_event(self, event: Event) -> EventResponse:
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

    def to_event_page(self, page: Page[Event]) -> Page[EventResponse]:
        return page.map(self.to_event)

    def to_event_detail(self, layout: EventLayoutSummaryInternal) -> EventDetailResponse:
        return EventDetailResponse(
            **self.to_event(layout.event).model_dump(),
            zones=[self.to_zone(zone) for zone in layout.zones],
        )

    def to_zone(self, zone_with_count: EventZoneWithSeatCountInternal) -> EventZoneResponse:
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

    def to_seat(self, seat: EventSeat) -> EventSeatResponse:
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

    def to_seat_page(self, page: Page[EventSeat]) -> Page[EventSeatResponse]:
        return page.map(self.to_seat)

    def to_layout_sync(
        self, counts: EventSeatSyncCountsInternal
    ) -> EventSeatLayoutSyncResponse:
        return EventSeatLayoutSyncResponse(
            created_count=counts.created_count,
            deleted_count=counts.deleted_count,
            seat_count=counts.seat_count,
        )
