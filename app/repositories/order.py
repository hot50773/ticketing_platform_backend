"""Gateway to the Order aggregate.

Instead of exposing one method per query, this hands out an OrderAggregate that
already owns a transaction: a use case enters it once, checks its rules and
writes, and everything commits together. See app/aggregates/order.py for the
concurrency contract behind create() and load_for_update().

Only genuinely collection-level queries stay here - ones that do not belong to
any single aggregate instance.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import select

from app.aggregates.order import OrderAggregate
from app.exceptions import (
    EVENT_NOT_FOUND_MSG,
    ORDER_NOT_FOUND_MSG,
    EntityNotFoundError,
)
from app.models import Event, EventSeat, Order, OrderTicket
from app.repositories._pagination import paginate
from app.schemas import Page, PaginationParams
from app.schemas.order import OrderCreate

# Every relationship is lazy="raise", so a detail read has to spell out the
# whole chain it intends to walk: order -> tickets -> seat -> zone. Named once
# here so the three entry points cannot drift apart.
#
# The tickets hop is the only one that filters and the only one loaded by a
# second SELECT. Both follow from it being the only collection in the chain:
#   - Filtering it shortens a list. A released ticket has to stay out of the
#     detail response, and the predicate is also what lets Postgres use
#     uq_order_ticket, whose index is partial on `deleted_at IS NULL`. The same
#     filter on either seat hop would resolve to None instead of a short list
#     and break the walk in OrderAggregate.tickets().
#   - joinedload on it would repeat the order row once per ticket. The two seat
#     hops are many-to-one, so they join without multiplying rows.
#
# The seat hops stay outer joins even though NOT NULL plus the foreign key make
# an inner join return the same rows today. The point is what happens if that
# stops holding: an outer join leaves event_seat as None and crashes
# OrderAggregate.tickets(), where an inner join would drop the ticket and leave
# the detail response quietly short of what the customer paid for.
TICKET_DETAIL_LOADER = (
    selectinload(Order.tickets.and_(OrderTicket.deleted_at == None))
    .joinedload(OrderTicket.event_seat)
    .joinedload(EventSeat.event_zone)
)


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
    async def create(self, payload: OrderCreate) -> AsyncIterator[OrderAggregate]:
        """Creates a pending order with its tickets, inside one transaction.

        Takes `FOR SHARE` on the event row before writing anything. This is the
        booking half of the contract documented in app/aggregates/event.py:
        shared locks do not block each other, so ticket grabbing stays fully
        concurrent, while an admin unpublishing or deleting the event waits on
        `FOR UPDATE` for the orders already in flight instead of racing them.
        Taking `FOR UPDATE` here instead would serialise every purchase for an
        event - do not do that.

        Raises EntityNotFoundError when the event does not exist or is deleted.
        """
        async with self.session_factory() as session:
            try:
                await self._lock_event_for_share(session, payload.event_id)

                order = Order(
                    user_id=payload.user_id,
                    event_id=payload.event_id,
                    checkout_amount=payload.checkout_amount,
                    payment_deadline=payload.payment_deadline,
                    comment=payload.comment,
                )
                session.add(order)
                await session.flush()

                session.add_all([
                    OrderTicket(
                        order_id=order.id,
                        event_seat_id=ticket.event_seat_id,
                        amount=ticket.amount,
                    )
                    for ticket in payload.tickets
                ])
                await session.flush()

                # Re-read through the loader chain: the tickets were just
                # inserted, but `order.tickets` is lazy="raise" and would not
                # pick them up on its own.
                yield OrderAggregate(
                    session, await self._get_and_check_order(session, order.id)
                )
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    # ----------------------------------------------------------------------- #
    # Collection queries
    # ----------------------------------------------------------------------- #
    async def get_orders(self, pagination: PaginationParams) -> Page[Order]:
        """One page of non-deleted orders ordered by id, plus the total count.

        No ticket loading: the list response carries order headers only, and a
        page of 50 orders would otherwise drag every seat behind them.
        """
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
    async def _lock_event_for_share(session: AsyncSession, event_id: int) -> None:
        """Takes `FOR SHARE` on the event row and asserts it is still live."""
        event = await session.scalar(
            select(Event)
            .where(Event.id == event_id)
            .where(Event.deleted_at == None)
            .with_for_update(read=True)
        )
        if not event:
            raise EntityNotFoundError(EVENT_NOT_FOUND_MSG.format(event_id=event_id))
