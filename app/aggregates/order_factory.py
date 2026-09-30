"""The factory for the Order aggregate.

Its own module rather than a second class in app/aggregates/order.py, because
the two are different kinds of thing and being neighbours kept implying they
were not: OrderAggregate is an aggregate root - an identity, a consistency
boundary, rules applied to rows that already exist - while this has no identity
at all. It is a transaction with an event locked in it, waiting for a use case
to tell it what the order should say.

Aggregate layer, not service layer: it holds a session, and services never see
one. It is handed out by OrderRepository.load_for_create() and only ever lives
inside that repository's transaction.

The one thing it may reach for is the Event aggregate, read-only (see the class
docstring). Keep the import direction one-way - order may reach for event,
never back - or the two aggregate modules become a cycle.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.aggregates.event import EventAggregate
from app.aggregates.order import TICKET_DETAIL_LOADER, OrderAggregate
from app.models import Order, OrderTicket
from app.schemas.event import EventSeatPriceInternal, EventStateInternal
from app.schemas.order import OrderCreate, OrderPricingInternal


class OrderAggregateFactory:
    """A factory for the Order aggregate, not an aggregate itself: no identity,
    no rules of its own, nothing but an open transaction and the event the
    order is being placed against.

    Creating an order is the one flow with rules to answer before there is an
    order to ask them about: is the event on sale, do these seats belong to it,
    what do they cost. Those reads have to happen in the same transaction as
    the insert - an event unpublished or repriced between a check in one
    transaction and an insert in another would leave a live order on a closed
    event, or on a price nobody offers - so OrderRepository.load_for_create() locks
    the event, hands out this object, and the use case turns it into an
    OrderAggregate by calling create().

    The reads themselves are the Event aggregate's, and stay there: this holds
    an EventAggregate over the same session and forwards to it, so the order
    side never queries event_seat or event_zone itself. Read-only, always - the
    one aggregate this transaction modifies is the Order. Keep it that way, and
    keep the import direction one-way: order may reach for event, never back.

    The event row arrives already locked `FOR SHARE`; see the concurrency
    contract at the top of app/aggregates/order.py for why it is a shared lock.
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
        """What OrderValidator judges the event on before it accepts an order."""
        return self._event.state

    async def seat_prices(self) -> list[EventSeatPriceInternal]:
        """The requested seats, each resolved to its zone's price."""
        return await self._event.seat_prices(self._payload.event_seat_ids)

    # ----------------------------------------------------------------------- #
    # Mutations
    # ----------------------------------------------------------------------- #
    async def create(self, pricing: OrderPricingInternal) -> OrderAggregate:
        """Inserts the order with its tickets and hands back the real aggregate.

        status and payment_status are left to their server defaults - a new
        order is always PENDING / PENDING, and the re-read below is what brings
        the generated values back into the session.

        The seats are not marked RESERVED here. A seat is only taken once the
        order is paid for; until then the hold lives in Redis, which is also
        what stops two orders from taking the same seat - the partial unique
        index on (order_id, event_seat_id) only stops one order from taking the
        same seat twice.
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
        await self._session.flush()

        return OrderAggregate(self._session, await self._reload(order.id))

    # ----------------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------------- #
    async def _reload(self, order_id: int) -> Order:
        """Re-reads the order through the loader chain.

        The tickets were just inserted into this session, but `order.tickets`
        is lazy="raise" and would not pick them up on its own. No soft-delete
        filter and no "not found" branch: the row was written by this very
        transaction, so it is there.
        """
        return await self._session.scalar(
            select(Order).where(Order.id == order_id).options(TICKET_DETAIL_LOADER)
        )
