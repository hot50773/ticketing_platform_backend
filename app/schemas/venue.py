"""API contracts for the Venue aggregate (Venue / Zone / Seat).

Field names follow the ORM models (`col`, `name`).
"""
from datetime import datetime

from sqlmodel import SQLModel, Field

from ._shared import _TableMetaMixin


class VenueCreate(SQLModel):
    """Schema representing API payload to create a new Venue."""
    name: str = Field(max_length=100)
    description: str = Field(max_length=1000)
    address: str = Field(max_length=100)


class VenueResponse(SQLModel):
    """Schema representing Venue details returned by API."""
    id: int
    name: str
    description: str
    address: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class SeatBase(SQLModel):
    """A seat's physical position inside a zone."""
    row: int = Field(ge=1)
    col: int = Field(ge=1)


class SeatCreate(SeatBase):
    """POST /zones/{zone_id}/seats.

    `serial_number` is NOT accepted: the backend numbers seats per zone,
    starting at 0 and continuing from the zone's current maximum.
    """
    pass


class SeatBatchCreateRequest(SQLModel):
    """POST /zones/{zone_id}/seats/batch payload wrapper.

    Seats are numbered in the order given.
    """
    seats: list[SeatCreate] = Field(min_length=1)


class SeatResponse(SeatBase, _TableMetaMixin):
    """Schema representing Seat details returned by API."""
    serial_number: int
    zone_id: int


class ZoneBase(SQLModel):
    """Schema representing API payload to create a new Zone."""
    name: str = Field(max_length=100)
    venue_id: int | None = Field(default=None)


class ZoneResponse(_TableMetaMixin, ZoneBase):
    """Schema representing Zone details returned by API."""
    pass


class ZoneCreate(ZoneBase):
    seats: list[SeatCreate]
