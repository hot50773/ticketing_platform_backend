from datetime import datetime

from sqlmodel import SQLModel, Field

from app.schemas import _TableMetaMixin


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


class ZoneBase(SQLModel):
    """Schema representing API payload to create a new Zone."""
    name: str = Field(max_length=100)
    venue_id: int | None = Field(default=None)


class ZoneResponse(_TableMetaMixin, ZoneBase):
    """Schema representing Zone details returned by API."""
    pass


class ZoneCreate(ZoneBase):
    seats: list[SeatBatchCreate]


class SeatBase(SQLModel):
    """Base schema for Seat."""
    serial_number: int
    row: int
    column: int


class SeatBatchCreate(SeatBase):
    """Schema representing API payload to create a new Seat."""
    pass


class SeatResponse(SeatBase, _TableMetaMixin):
    """Schema representing Seat details returned by API."""
    zone_id: int


class ZoneUpdate(ZoneBase):
    id: int
    seats: list[SeatBatchCreate]
