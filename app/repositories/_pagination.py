"""Shared LIMIT/OFFSET helper for repository list queries."""
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.schemas import Page, PaginationParams

T = TypeVar("T")


async def paginate(
    session: AsyncSession,
    stmt: Select,
    pagination: PaginationParams,
    item_type: type[T],
) -> Page[T]:
    """Runs `stmt` twice: as COUNT(*) for the total, then windowed for the rows.

    ORDER BY is stripped from the count query since it cannot affect the result.
    """
    total = await session.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    windowed = stmt.limit(pagination.page_size).offset(pagination.offset)
    items = list((await session.execute(windowed)).scalars().all())
    return Page[item_type](
        items=items,
        total=total or 0,
        page=pagination.page,
        page_size=pagination.page_size,
    )
