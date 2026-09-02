"""API contracts for the Event aggregate (Event / EventZone / EventSeat).

Field names follow the ORM models (`start_at`, `end_at`, `col`).
Schemas suffixed `Internal` never cross the API boundary: they are the
transport objects between Repository and Service (no dict / tuple passing).

Seat lists are deliberately kept out of the event/zone responses: a 萬人 venue
has 10k+ EventSeat rows, so the layout is fetched per zone through its own
endpoint instead.
"""
from datetime import datetime

from sqlmodel import SQLModel, Field

from app.enums import SeatStatus
from app.models import Event, EventZone
from ._shared import UtcDatetime, _TableMetaMixin

# EventZone.price is nullable: NULL means "not configured yet", which blocks
# publishing. 0 is therefore a valid price (a genuinely free zone).


# --------------------------------------------------------------------------- #
# Requests - Event
# --------------------------------------------------------------------------- #
class EventCreate(SQLModel):
    """POST /events.

    Zones and seats are not part of the payload: they are cloned from the
    venue's Zone/Seat template. Prices start unset and must be filled in
    before the event can be published.
    """
    name: str = Field(max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    start_at: UtcDatetime
    end_at: UtcDatetime
    venue_id: int


class EventProfileUpdate(SQLModel):
    """PATCH /events/{event_id}/profile."""
    name: str = Field(max_length=100)
    description: str | None = Field(default=None, max_length=1000)


class EventHoldingTimeUpdate(SQLModel):
    """PATCH /events/{event_id}/holding_time. Rejected once published."""
    start_at: UtcDatetime
    end_at: UtcDatetime


class EventStatusUpdate(SQLModel):
    """PATCH /events/{event_id}/status."""
    is_active: bool


# --------------------------------------------------------------------------- #
# Requests - EventZone / EventSeat
# --------------------------------------------------------------------------- #
class EventSeatLayout(SQLModel):
    """One seat position inside a zone layout payload.

    `serial_number` is NOT accepted: the backend numbers a zone's seats
    contiguously from 1, and that number is the seat's Redis bitmap offset
    (offset = serial_number - 1). `(row, col)` is therefore the key the client
    identifies a seat by.
    """
    row: int = Field(ge=1)
    col: int = Field(ge=1)


class EventZoneCreate(SQLModel):
    """POST /events/{event_id}/event_zones. Rejected once published.

    Seats are numbered 1..N by the backend in `(row, col)` order.
    """
    name: str = Field(max_length=100)
    price: int | None = Field(default=None, ge=0, description="None = 尚未設定票價")
    seats: list[EventSeatLayout] = Field(default_factory=list)


class EventZoneProfileUpdate(SQLModel):
    """PATCH /events/{event_id}/event_zones/{event_zone_id}/profile.

    Both name and price stay editable after the event is published.
    """
    name: str = Field(max_length=100)
    price: int | None = Field(default=None, ge=0, description="None = 尚未設定票價")


class EventSeatLayoutUpdate(SQLModel):
    """PATCH /events/{event_id}/event_zones/{event_zone_id}/seats.

    Full-replacement semantics: `seats` is the desired final layout of the
    zone, keyed on `(row, col)`. Positions absent from the payload are
    soft-deleted; new positions are inserted; the whole zone is then
    renumbered 1..N in `(row, col)` order.

    Renumbering is only safe because this endpoint is rejected once the event
    is published - after publishing, serial_number (= the Redis bitmap offset)
    must never move.
    """
    seats: list[EventSeatLayout]


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class EventZoneResponse(_TableMetaMixin, SQLModel):
    event_id: int
    name: str
    price: int | None
    seat_count: int


class EventResponse(_TableMetaMixin, SQLModel):
    name: str
    description: str | None = None
    start_at: datetime
    end_at: datetime
    venue_id: int
    is_active: bool


class EventDetailResponse(EventResponse):
    """Event plus its zones. Seats are fetched per zone, not inlined."""
    zones: list[EventZoneResponse] = Field(default_factory=list)


class EventSeatResponse(_TableMetaMixin, SQLModel):
    event_zone_id: int
    serial_number: int
    row: int
    col: int
    status: SeatStatus


class EventSeatLayoutSyncResponse(SQLModel):
    """Outcome of a batch layout replacement."""
    created_count: int
    deleted_count: int
    renumbered_count: int
    seat_count: int


# --------------------------------------------------------------------------- #
# Internal transport (Repository -> Service)
# --------------------------------------------------------------------------- #
class EventZoneWithSeatCountInternal(SQLModel):
    zone: EventZone
    seat_count: int


class EventLayoutInternal(SQLModel):
    """An event together with its zones and per-zone seat counts."""
    event: Event
    zones: list[EventZoneWithSeatCountInternal] = Field(default_factory=list)


class EventZonePricingInternal(SQLModel):
    """Price projection used by the publish rule."""
    id: int
    name: str
    price: int | None


class EventOrderStatsInternal(SQLModel):
    """Order counters used by the unpublish / delete rules."""
    pending_count: int
    confirmed_count: int


class EventSeatSyncCountsInternal(SQLModel):
    """Row counts produced by a layout replacement transaction."""
    created_count: int
    deleted_count: int
    renumbered_count: int
    seat_count: int
