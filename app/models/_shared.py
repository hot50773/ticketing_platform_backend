from datetime import datetime

from sqlalchemy import DateTime, func
from sqlmodel import SQLModel, Field


# SQLModel 把 sa_type 標註成 `type[Any]`，但 get_sqlalchemy_type() 是原封不動回傳、
# 再交給 Column()，而 Column() 型別「實例」也收。要帶 timezone=True 就必須傳實例，
# 因此下面的 sa_type 需要 type: ignore 壓掉過窄的標註。
class TableBase(SQLModel):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore
        default_factory=func.now,
    )
    # onupdate, not server_onupdate: the latter only *tells* SQLAlchemy that
    # the database generates the value, it emits no SQL. With no DB trigger in
    # place, server_onupdate left updated_at frozen at its insert value.
    updated_at: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore
        default_factory=func.now,
        sa_column_kwargs={"onupdate": func.now()},
    )

class SoftDeletable(SQLModel):
    deleted_at: datetime | None = Field(
        sa_type=DateTime(timezone=True),  # type: ignore
    )
