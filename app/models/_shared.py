from datetime import datetime, timezone
from sqlmodel import SQLModel, Field
from sqlalchemy import DateTime

class TableBase(SQLModel):
    id: int = Field(primary_key=True)
    created_at: datetime = Field(
        sa_type=DateTime(timezone=True),
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        sa_type=DateTime(timezone=True),
        default_factory=lambda: datetime.now(timezone.utc)
    )

class SoftDeletable(SQLModel):
    deleted_at: datetime | None = Field(
        sa_type=DateTime(timezone=True),
        default=None
    )
