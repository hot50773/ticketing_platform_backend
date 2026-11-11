from sqlmodel import SQLModel

from ._shared import _TableMetaMixin


class CustomerResponse(SQLModel, _TableMetaMixin):
    name: str
    email: str


class CustomerCreateInternal(SQLModel):
    name: str
    email: str
    google_sub: str
