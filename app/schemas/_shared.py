from datetime import datetime, timezone
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, Field

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200


def _to_utc(value: datetime) -> datetime:
    """Normalises any incoming datetime to timezone-aware UTC.

    Whatever offset the client sends is converted; a naive datetime is assumed
    to already be UTC. Every datetime inside the system is aware UTC, matching
    the timestamptz columns.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# Use for every datetime that enters through an API payload.
UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]


class PaginationParams(BaseModel):
    """Query params shared by every list endpoint.

    Declare as `Annotated[PaginationParams, Query()]` so FastAPI flattens it
    into `?limit=&offset=` instead of expecting a body.
    """
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Envelope returned by every list endpoint.

    `total` is the row count ignoring limit/offset, so clients can paginate.
    """
    items: list[T]
    total: int
    limit: int
    offset: int


class _TableMetaMixin:
    id: int
    created_at: datetime
    updated_at: datetime
