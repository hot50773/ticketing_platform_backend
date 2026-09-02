from sqlmodel import SQLModel, Field


class UserCreate(SQLModel):
    """Schema representing the API input required to create a new user."""
    name: str = Field(max_length=100)
    email: str | None = Field(default=None, max_length=100)
    password: str | None = Field(default=None, max_length=100)
