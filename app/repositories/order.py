"""Gateway to the Order aggregate.

Instead of exposing one method per query, this hands out an OrderAggregate that
already owns a transaction: a use case enters it once, checks its rules and
writes, and everything commits together. load_for_create() hands out a factory
instead, the order row not existing yet; like load_for_update(), it loads and
locks but does not itself perform the operation it is named for.

Only genuinely collection-level queries stay here - ones that do not belong to
any single aggregate instance.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.aggregates.event import EventAggregate
from app.aggregates.order import TICKET_DETAIL_LOADER, OrderAggregate
from app.aggregates.order_factory import OrderAggregateFactory
from app.exceptions import (
    EVENT_NOT_FOUND_MSG,
    ORDER_NOT_FOUND_MSG,
    EntityNotFoundError,
)
from app.models import Event, Order
from app.repositories._pagination import paginate
from app.schemas import Page, PaginationParams
from app.schemas.order import OrderCreate


class OrderRepository:
    """Opens transactions and hands the use case an OrderAggregate inside one."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    # ----------------------------------------------------------------------- #
    # Aggregate access
    # ----------------------------------------------------------------------- #
    @asynccontextmanager
    async def load(self, order_id: int) -> AsyncIterator[OrderAggregate]:
        async with self.session_factory() as session:
            try:
                yield OrderAggregate(session, await self._get_and_check_order(session, order_id))
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def load_for_update(self, order_id: int) -> AsyncIterator[OrderAggregate]:
        """Write access. Locks the order row with `FOR UPDATE`, commits on a
        clean exit and rolls back explicitly on any exception.

        The lock matters most on the payment path: two concurrent pay requests
        for the same order must not both read PENDING and both charge.
        """
        async with self.session_factory() as session:
            try:
                order = await self._get_and_check_order(session, order_id, for_update=True)
                yield OrderAggregate(session, order)
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def load_for_release(self, order_id: int) -> AsyncIterator[OrderAggregate]:
        """Shared, as in load_for_create(): bookings never queue behind a release."""
        async with self.session_factory() as session:
            try:
                order = await self._get_and_check_order(session, order_id, for_update=True)
                event = await self._lock_event_for_share(session, order.event_id)
                yield OrderAggregate(session, order, EventAggregate(session, event))
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def load_for_create(
        self, payload: OrderCreate
    ) -> AsyncIterator[OrderAggregateFactory]:
        """Locks the event and hands out the factory. Inserts nothing - that is
        OrderAggregateFactory.create().

        `FOR SHARE`, not `FOR UPDATE`: an admin unpublishing the event still
        waits for in-flight orders, but purchases do not queue behind each
        other. `FOR UPDATE` here would serialise every purchase for an event.

        Raises EntityNotFoundError when the event does not exist or is deleted.
        """
        async with self.session_factory() as session:
            try:
                event = await self._lock_event_for_share(session, payload.event_id)
                yield OrderAggregateFactory(
                    session, EventAggregate(session, event), payload
                )
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    # ----------------------------------------------------------------------- #
    # Collection queries
    # ----------------------------------------------------------------------- #
    async def get_orders(self, pagination: PaginationParams) -> Page[Order]:
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
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .where(Order.deleted_at == None)
            .options(TICKET_DETAIL_LOADER)
        )
        if for_update:
            # Only the order row is locked. selectinload issues its own SELECT
            # for the tickets, which FOR UPDATE would not cover anyway - and
            # locking a join of order + ticket + seat + zone would be a far
            # wider lock than this use case needs.
            stmt = stmt.with_for_update(of=Order)
        order = await session.scalar(stmt)
        if not order:
            raise EntityNotFoundError(ORDER_NOT_FOUND_MSG.format(order_id=order_id))
        return order

    @staticmethod
    async def _lock_event_for_share(session: AsyncSession, event_id: int) -> Event:
        """Takes `FOR SHARE` on the event row and asserts it still exists.

        Returns the locked row rather than discarding it - re-reading would
        drop the guarantee that what the rules judged is what the lock holds.
        """
        event = await session.scalar(
            select(Event)
            .where(Event.id == event_id)
            .where(Event.deleted_at == None)
            .with_for_update(read=True)
        )
        if not event:
            raise EntityNotFoundError(EVENT_NOT_FOUND_MSG.format(event_id=event_id))
        return event
