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


