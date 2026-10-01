"""API contracts for the Event aggregate (Event / EventZone / EventSeat).

Field names follow the ORM models (`start_at`, `end_at`, `col`).
Schemas suffixed `Internal` never cross the API boundary: they are the
transport objects between Repository and Service (no dict / tuple passing).

Seat lists are deliberately kept out of the event/zone responses: a 萬人 venue
has 10k+ EventSeat rows, so the layout is fetched per zone through its own
endpoint instead.
"""
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator
from sqlmodel import SQLModel, Field

from app.enums import SeatStatus
from app.models import Event, EventZone
from ._shared import UtcDatetime, _TableMetaMixin

# EventZone.price is nullable: NULL means "not configured yet", which blocks
# publishing. 0 is therefore a valid price (a genuinely free zone).


# --------------------------------------------------------------------------- #
# Requests - Event
# --------------------------------------------------------------------------- #
class EventHoldingTime(SQLModel):
    """The window an event is held in.

    Shared base of every payload that carries a holding time, so the rule
    validator can take one type and both call sites pass their payload through
    unchanged.
    """
    start_at: UtcDatetime
    end_at: UtcDatetime


class EventCreate(EventHoldingTime):
    """POST /events.

    Zones and seats are not part of the payload: they are cloned from the
    venue's Zone/Seat template. Prices start unset and must be filled in
    before the event can be published.
    """
    name: str = Field(max_length=100)
    description: str = Field(max_length=1000)
    venue_id: int


class EventProfileUpdate(SQLModel):
    """PATCH /events/{event_id}/profile."""
    name: str = Field(max_length=100)
    description: str = Field(max_length=1000)


class EventHoldingTimeUpdate(EventHoldingTime):
    """PATCH /events/{event_id}/holding_time. Rejected once published."""
    pass


# --------------------------------------------------------------------------- #
# Requests - EventZone / EventSeat
# --------------------------------------------------------------------------- #
class EventSeatLayout(SQLModel):
    """One seat position inside a zone layout payload.

    `serial_number` is NOT accepted: it *is* the seat's Redis bitmap bit
    offset - 0-based, no arithmetic in between - and stays backend-owned.
    `(row, col)` is therefore the key the client identifies a seat by.

    Numbers are unique per zone but NOT contiguous - soft-deleting a seat
    leaves a hole, and holes are never reclaimed. A Redis bitmap for a zone
    must therefore be sized by max(serial_number) + 1, not by the seat count.
    """
    row: int = Field(ge=1)
    col: int = Field(ge=1)


def _reject_duplicate_positions(seats: list[EventSeatLayout]) -> list[EventSeatLayout]:
    """(row, col) is a seat's identity, so a layout payload cannot repeat one."""
    positions = [(seat.row, seat.col) for seat in seats]
    if len(positions) != len(set(positions)):
        raise ValueError("duplicate (row, col) positions in seat layout")
    return seats


SeatLayout = Annotated[list[EventSeatLayout], AfterValidator(_reject_duplicate_positions)]


class EventZoneCreate(SQLModel):
    """POST /events/{event_id}/event_zones. Rejected once published.

    This zone has no venue template, so its seats are numbered from 1 in
    payload order.
    """
    name: str = Field(max_length=100)
    price: int | None = Field(default=None, ge=0, description="None = 尚未設定票價")
    seats: SeatLayout = Field(default_factory=list)


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
    soft-deleted, new positions are inserted, and everything else is left
    untouched.

    Surviving seats keep their serial_number: the layout is never renumbered,
    so a Redis offset can only ever be added or retired, never reassigned to a
    different seat. New seats take max(serial_number) + 1 (0 in an empty
    zone), counting soft-deleted rows so a retired offset is never recycled.
    """
    seats: SeatLayout


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class EventZoneResponse(_TableMetaMixin, SQLModel):
    event_id: int
    name: str
    price: int | None
    seat_count: int


class EventResponse(SQLModel, _TableMetaMixin):
    name: str
    description: str
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
    """Outcome of a batch layout replacement.

    There is no update count: `(row, col)` is the seat's identity, so a seat
    is either added, removed, or left exactly as it was.
    """
    created_count: int
    deleted_count: int
    seat_count: int


# --------------------------------------------------------------------------- #
# Internal transport (Repository -> Service)
# --------------------------------------------------------------------------- #
class EventZoneWithSeatCountInternal(SQLModel):
    zone: EventZone
    seat_count: int

class EventSeatInternal(SQLModel):
    id: int
    serial_number: int
    row: int
    col: int
    status: SeatStatus

class EventZoneWithSeatInternal(SQLModel):
    id: int
    name: str
    price: int | None
    seats: list[EventSeatInternal]


class EventLayoutInternal(SQLModel):
    event: Event
    zones: list[EventZoneWithSeatInternal] = Field(default_factory=list)


class EventLayoutSummaryInternal(SQLModel):
    """An event together with its zones and per-zone seat counts."""
    event: Event
    zones: list[EventZoneWithSeatCountInternal] = Field(default_factory=list)


class EventStateInternal(SQLModel):
    """An event's state, as handed to the rule validators. The admin rules read
    is_active; the order rules read both (orderable = published, not started)."""
    id: int
    is_active: bool
    start_at: datetime


class EventSeatPriceInternal(SQLModel):
    """One seat resolved to its zone's price - what the booking path needs,
    against the whole layout the publish rules read.

    NULL price means the zone was never priced; the order rules reject it.
    """
    event_seat_id: int
    event_zone_id: int
    price: int | None


class EventOrderStatsInternal(SQLModel):
    """Order counter used by the unpublish / delete rules.

    Counts orders that are NOT soft-deleted, whatever their status - CANCELLED
    included. An order is soft-deleted when the system releases it (expired
    cart), and only those stop blocking; a user-cancelled order still counts.
    """
    active_order_count: int


class EventSeatDiffInternal(SQLModel):
    """What a layout replacement has to change. A seat that keeps its position
    appears in neither list - it is never renumbered or rewritten."""
    added: list[EventSeatLayout]
    retired_seat_ids: list[int]


class EventSeatSyncCountsInternal(SQLModel):
    """Row counts produced by a layout replacement transaction."""
    created_count: int
    deleted_count: int
    seat_count: int
