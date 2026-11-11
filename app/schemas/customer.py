from datetime import datetime

from sqlmodel import SQLModel


class CustomerResponse(SQLModel):
    id: int
    created_at: datetime
    updated_at: datetime
    name: str
    email: str


class CustomerCreateInternal(SQLModel):
    name: str
    email: str
    google_sub: str
