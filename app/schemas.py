from sqlmodel import SQLModel, Field

class UserCreate(SQLModel):
    """Schema representing the API input required to create a new user/nickname."""
    name: str = Field(max_length=100)
