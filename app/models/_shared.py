from datetime import datetime, timezone
from sqlmodel import SQLModel, Field

class TableBase(SQLModel):
    id: int = Field(primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

class SoftDeletable(SQLModel):
    deleted_at: datetime | None = Field(default=None)
