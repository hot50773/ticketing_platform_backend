from datetime import datetime

from sqlalchemy import DateTime, func, Column
from sqlmodel import SQLModel, Field


class TableBase(SQLModel):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True),
            server_default=func.now()
        ),
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )

class SoftDeletable(SQLModel):
    deleted_at: datetime | None = Field(
        sa_column=Column(DateTime(timezone=True)),
    )
