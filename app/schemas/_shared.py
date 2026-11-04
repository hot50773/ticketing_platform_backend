from collections.abc import Callable
from datetime import datetime, timezone
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, Field

from app.constants import DEFAULT_PAGE_SIZE
from app.enums import PageSize


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
    into `?page=&page_size=` instead of expecting a body.
    """
    page: int = Field(default=1, ge=1)
    page_size: PageSize = Field(default=DEFAULT_PAGE_SIZE)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Envelope returned by every list endpoint.

    `total` is the row count across all pages, so clients can paginate.
    """
    items: list[T]
    total: int
    page: int
    page_size: int

    def map(self, transform: Callable[[T], object]) -> "Page":
        """Re-wraps the page with every item transformed, counters untouched.

        Saves each caller from restating total/page/page_size when turning a page
        of ORM rows into a page of response schemas.
        """
        return Page(
            items=[transform(item) for item in self.items],
            total=self.total,
            page=self.page,
            page_size=self.page_size,
        )


class _TableMetaMixin:
    id: int
    created_at: datetime
    updated_at: datetime
