from datetime import datetime, timezone

from sqlmodel import SQLModel, Field


class TableBase(SQLModel, table=True):
    id: int = Field(primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

class SoftDeletable(SQLModel):
    deleted_at: datetime | None = Field(default=None)
