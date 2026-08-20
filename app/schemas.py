from sqlmodel import SQLModel, Field

class UserCreate(SQLModel):
    """Schema representing the API input required to create a new user."""
    name: str = Field(max_length=100)

class LockSeatRequest(SQLModel):
    """Request schema for locking a seat."""
    user_id: int

class UnlockSeatRequest(SQLModel):
    """Request schema for unlocking a seat."""
    user_id: int
