"""Factory for the Order aggregate: a transaction with an event locked in it,
waiting to be told what the order should say.

Its own module so nobody reads it as a second aggregate. Import direction is
one-way - order reaches for event, never back.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.aggregates.event import EventAggregate
from app.aggregates.order import TICKET_DETAIL_LOADER, OrderAggregate
from app.models import Order, OrderTicket
from app.schemas.event import (
    EventSeatsPricingInternal,
    EventStateInternal,
    EventLayoutInternal,
)
from app.schemas.order import OrderCreate, OrderCreateInternal


class OrderAggregateFactory:
    """Not an aggregate: no identity, no rules, no commit - the repository owns
    the transaction.

    The event reads belong to EventAggregate and stay there; this forwards to
    it, read-only. They share the insert's transaction because an event
    unpublished or repriced in between would leave an order on a closed event,
    or on a price nobody offers.
    """

    def __init__(
        self, session: AsyncSession, event: EventAggregate, payload: OrderCreate
    ):
        self._session = session
        self._event = event
        self._payload = payload

    # ----------------------------------------------------------------------- #
    # State for the rules
    # ----------------------------------------------------------------------- #
    @property
    def event_state(self) -> EventStateInternal:
        return self._event.state

    async def seats_pricing(self) -> EventSeatsPricingInternal:
        return await self._event.seat_prices(self._payload.event_seat_ids)

    async def event_layout(self) -> EventLayoutInternal:
        return await self._event.event_layout()
    # ----------------------------------------------------------------------- #
    # Mutations
    # ----------------------------------------------------------------------- #
    async def create(self, pricing: OrderCreateInternal) -> OrderAggregate:
        """Inserts the order and its tickets. status / payment_status are left
        to their server defaults, which the re-read brings back.

        Nothing here stops two orders taking the same seat: uq_order_ticket is
        per-order, so the Redis hold is what has to.
        """
        order = Order(
            user_id=self._payload.user_id,
            event_id=self._event.event.id,
            checkout_amount=pricing.checkout_amount,
            payment_deadline=pricing.payment_deadline,
            comment=self._payload.comment,
        )
        self._session.add(order)
        await self._session.flush()

        self._session.add_all([
            OrderTicket(
                order_id=order.id,
                event_seat_id=ticket.event_seat_id,
                amount=ticket.amount,
            )
            for ticket in pricing.tickets
        ])

        await self._event.check_and_reserve_seats(
            event_seat_ids=[ticket.event_seat_id for ticket in pricing.tickets],
        )
        await self._session.flush()

        return OrderAggregate(self._session, await self._reload(order.id))

    # ----------------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------------- #
    async def _reload(self, order_id: int) -> Order:
        """`order.tickets` is lazy="raise" and would not pick up the rows this
        transaction just inserted. No not-found branch - we wrote it."""
        return await self._session.scalar(
            select(Order).where(Order.id == order_id).options(TICKET_DETAIL_LOADER)
        )
