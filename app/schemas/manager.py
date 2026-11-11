from datetime import datetime

from sqlmodel import Field, SQLModel

from app.enums import ManagerRole
from ._shared import PaginationParams
from .auth import Email


class ManagerCreate(SQLModel):
    name: str = Field(max_length=100)
    email: Email = Field(max_length=100)
    password: str = Field(min_length=8, max_length=100)


class ManagerListParams(PaginationParams):
    role: ManagerRole | None = None


class ManagerResponse(SQLModel):
    id: int
    created_at: datetime
    updated_at: datetime
    name: str
    email: str
    role: ManagerRole
    is_active: bool


class ManagerCreateInternal(SQLModel):
    name: str
    email: str
    hashed_password: str
    role: ManagerRole
