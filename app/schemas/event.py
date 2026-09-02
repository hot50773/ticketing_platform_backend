from datetime import datetime
from sqlmodel import SQLModel, Field
from ._shared import _TableMetaMixin


class EventBase(SQLModel):
    """Base schema for Event."""
    name: str = Field(max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    start_time: datetime
    end_time: datetime
    venue_id: int

class EventZoneBase(SQLModel):
    """Schema representing API payload to create a new EventZone."""
    name: str = Field(max_length=100)
    price: int = Field(default=0)

class EventSeatBase(SQLModel):
    """Schema representing API payload to create a new EventSeat."""
    row: int
    col: int
    serial_number: int

class EventResponse(EventBase, _TableMetaMixin):
    """Schema representing Event details returned by API."""
    pass

class EventUpdateTime(SQLModel):
    start_time: datetime
    end_time: datetime

class EventUpdateProfile(SQLModel):
    name: str = Field(max_length=100)
    description: str | None = Field(max_length=1000)

class EventSeatResponse(EventSeatBase, _TableMetaMixin):
    pass

class EventZoneResponse(EventZoneBase, _TableMetaMixin):
    """Schema representing EventZone details returned by API."""
    event_seats: EventSeatResponse

class EventSeatAutoGenerate(EventSeatBase):
    source_seat_id: int | None = Field(default=None)

class EventZoneAutoGenerate(EventZoneBase):
    source_zone_id: int | None = Field(default=None)
    seats: list[EventSeatAutoGenerate]

class EventCreate(EventBase):
    zones: list[EventZoneAutoGenerate]

class EventSeatCreate(EventSeatBase):
    pass

class EventZoneCreate(EventZoneBase):
    event_id: int
    seats: list[EventSeatCreate]


class EventZoneUpdate(EventZoneBase):
    id: int


class EventZoneDeleteBatch(SQLModel):
    ids: list[int]

