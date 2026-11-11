from sqlmodel import Field, SQLModel

from app.enums import ManagerRole
from ._shared import PaginationParams, _TableMetaMixin
from .auth import Email


class ManagerCreate(SQLModel):
    name: str = Field(max_length=100)
    email: Email = Field(max_length=100)
    password: str = Field(min_length=8, max_length=100)


class ManagerLoginForm(SQLModel):
    """POST /admin/auth/login. Field names follow the OAuth2 password flow, so
    Swagger's Authorize can log in; `username` carries the email."""
    username: Email
    password: str


class ManagerListParams(PaginationParams):
    role: ManagerRole | None = None


class ManagerResponse(SQLModel, _TableMetaMixin):
    name: str
    email: str
    role: ManagerRole
    is_active: bool


class ManagerCreateInternal(SQLModel):
    name: str
    email: str
    hashed_password: str
    role: ManagerRole
