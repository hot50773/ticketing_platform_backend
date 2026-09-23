"""Gateway to the Order aggregate.

Instead of exposing one method per query, this hands out an OrderAggregate that
already owns a transaction: a use case enters it once, checks its rules and
writes, and everything commits together. See app/aggregates/order.py for the
concurrency contract behind load_for_update().

Only genuinely collection-level queries stay here - ones that do not belong to
any single aggregate instance.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.schemas.order import OrderCreate
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.aggregates.order import OrderAggregate
from app.exceptions import (
    EVENT_NOT_FOUND_MSG,
    EntityNotFoundError,
)
from app.models import Order
from app.repositories._pagination import paginate
from app.schemas import Page, PaginationParams


class OrderRepository:
    """Opens transactions and hands the use case an OrderAggregate inside one."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    # ----------------------------------------------------------------------- #
    # Aggregate access
    # ----------------------------------------------------------------------- #
    @asynccontextmanager
    async def load(self, order_id: int) -> AsyncIterator[OrderAggregate]:
        """Read-only access. Takes no row lock.

        The rollback is belt-and-braces on a read path - there should be
        nothing pending to undo. It is here so all three entry points unwind
        the same way, and so a write accidentally issued through `load()` is
        discarded loudly rather than left to the pool.
        """
        async with self.session_factory() as session:
            try:
                yield OrderAggregate(session, await self._get_and_check_order(session, order_id))
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def load_for_update(self, order_id: int) -> AsyncIterator[OrderAggregate]:
        async with self.session_factory() as session:
            try:
                order = await self._get_and_check_order(session, order_id, for_update=True)
                yield OrderAggregate(session, order)
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def create(self, payload: OrderCreate) -> AsyncIterator[OrderAggregate]:
        """Creates an unpublished order, clones the venue's layout into it, and
        hands back the aggregate still inside the same transaction.

        OrderSeat.serial_number is copied straight from Seat.serial_number, so a
        zone's numbering can have holes where template seats were deleted.
        Cloning uses `INSERT ... SELECT` rather than ORM row-by-row inserts: a
        10k-seat venue would otherwise round-trip 10k objects through Python.

        Raises EntityNotFoundError when the venue does not exist or is deleted.
        """
        async with self.session_factory() as session:
            try:
                order = Order(
                    name=payload.name,
                    description=payload.description,
                    start_at=payload.start_at,
                    end_at=payload.end_at,
                    is_active=False,
                    venue_id=payload.venue_id,
                )
                session.add(order)
                await session.flush()
                await session.refresh(order)

                yield OrderAggregate(session, order)
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    # ----------------------------------------------------------------------- #
    # Collection queries
    # ----------------------------------------------------------------------- #
    async def get_orders(self, pagination: PaginationParams) -> Page[Order]:
        """One page of non-deleted orders ordered by id, plus the total count."""
        async with self.session_factory() as session:
            stmt = select(Order).where(Order.deleted_at == None).order_by(Order.id)
            return await paginate(session, stmt, pagination, Order)

    # ----------------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------------- #
    @staticmethod
    async def _get_and_check_order(
        session: AsyncSession, order_id: int, for_update: bool = False
    ) -> Order:
        stmt = select(Order).where(Order.id == order_id).where(Order.deleted_at == None)
        if for_update:
            stmt = stmt.with_for_update()
        order = await session.scalar(stmt)
        if not order:
            raise EntityNotFoundError(EVENT_NOT_FOUND_MSG.format(order_id=order_id))
        return order