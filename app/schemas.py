from datetime import datetime
from sqlmodel import SQLModel, Field
from app.models._models import EventSeat, Seat, EventZone

class UserCreate(SQLModel):
    """Schema representing the API input required to create a new user."""
    name: str = Field(max_length=100)

class LockSeatRequest(SQLModel):
    """Request schema for locking a seat."""
    user_id: int

class UnlockSeatRequest(SQLModel):
    """Request schema for unlocking a seat."""
    user_id: int

class LockSeatsRequest(SQLModel):
    """Request schema for locking multiple seats."""
    user_id: int
    seat_ids: list[int]

class UnlockSeatsRequest(SQLModel):
    """Request schema for unlocking multiple seats."""
    user_id: int
    seat_ids: list[int]

class EventSeatDetail(SQLModel):
    """Structured data schema representing event seat, seat details, and optional zone info."""
    event_seat: EventSeat
    seat: Seat
    event_zone: EventZone | None = None

class VenueCreate(SQLModel):
    """Schema representing API payload to create a new Venue."""
    name: str = Field(max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    address: str = Field(max_length=100)

class VenueResponse(SQLModel):
    """Schema representing Venue details returned by API."""
    id: int
    name: str
    description: str | None = None
    address: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

class ZoneCreate(SQLModel):
    """Schema representing API payload to create a new Zone."""
    code: str = Field(max_length=100)
    venue_id: int | None = Field(default=None)

class ZoneResponse(SQLModel):
    """Schema representing Zone details returned by API."""
    id: int
    code: str
    venue_id: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

class SeatCreate(SQLModel):
    """Schema representing API payload to create a new Seat."""
    serial_number: int
    row: int = Field(default=0)
    column: int = Field(default=0)
    zone_id: int | None = Field(default=None)

class SeatResponse(SQLModel):
    """Schema representing Seat details returned by API."""
    id: int
    serial_number: int
    row: int
    column: int
    zone_id: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

class SeatBatchCreate(SQLModel):
    """Schema representing API payload to batch create Seats under a Zone."""
    seats: list[SeatCreate]



