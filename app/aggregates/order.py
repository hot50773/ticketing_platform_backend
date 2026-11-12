"""The Order aggregate root: Order plus its tickets.

Loaded by OrderRepository inside a transaction and holding that session for its
whole lifetime, so a rule checked here cannot go stale before the write that
depends on it. Services never see the session.

The aggregate carries no business rules - it only exposes the state the rules
need (always as `*Internal` DTOs, never ORM rows) and applies the mutations a
use case asks for. Judgement stays in OrderValidator.

Creating an order goes through app/aggregates/order_factory.py instead - there
is nothing to apply rules to yet.

CONCURRENCY CONTRACT
--------------------
The admin paths on the event side take `FOR UPDATE` on the event row. This
aggregate is the other half of that contract: creating or paying for an order
MUST take `FOR SHARE` on the same event row first (see OrderRepository), so an
admin unpublishing an event waits for in-flight orders instead of racing them.
Shared locks do not block each other, so ticket grabbing stays concurrent.

EAGER LOADING
-------------
Every relationship is `lazy="raise"`, so `order.tickets` only works when
TICKET_DETAIL_LOADER below loaded it. Deliberate: a lazy load here fires from
outside the greenlet owning the connection and surfaces as MissingGreenlet
somewhere unrelated.
"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.aggregates.event import EventAggregate
from app.enums import OrderStatus, PaymentStatus
from app.libs.clock.functional import now_utc
from app.models import EventSeat, Order, OrderTicket
from app.schemas.order import (
    OrderDelete,
    OrderPay,
    OrderStateInternal,
    OrderTicketInternal,
    OrderWithTicketsInternal,
)


# Every relationship is lazy="raise", so a detail read has to spell out the
# whole chain it intends to walk: order -> tickets -> seat -> zone. Named once
# here so every entry point that returns an order detail walks the same one.
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
# Back-office reads: soft_delete_cascade deletes the tickets with the order, and
# the audit needs to see which seats a deleted order held.
TICKET_HISTORY_LOADER = (
    selectinload(Order.tickets)
    .joinedload(OrderTicket.event_seat)
    .joinedload(EventSeat.event_zone)
)


class OrderAggregate:
    """One order and its tickets, bound to the transaction that loaded it."""

    def __init__(
        self, session: AsyncSession, order: Order, event: EventAggregate | None = None
    ):
        self._session = session
        self._order = order
        # FIXME: load_for_release() should yield both aggregates and let
        #  OrderService call each, instead of nesting the event in here.
        self._event = event

    @property
    def order(self) -> Order:
        """The root row. Read-only as far as callers are concerned - go through
        the mutators so every change is flushed and refreshed consistently."""
        return self._order

    # ----------------------------------------------------------------------- #
    # State for the rules
    # ----------------------------------------------------------------------- #
    @property
    def state(self) -> OrderStateInternal:
        """What OrderValidator judges on. Deliberately not the aggregate
        itself: a rule that needs an aggregate needs a database to test."""
        return OrderStateInternal(
            id=self._order.id,
            status=self._order.status,
            payment_status=self._order.payment_status,
            payment_deadline=self._order.payment_deadline,
        )

    # ----------------------------------------------------------------------- #
    # Reads
    # ----------------------------------------------------------------------- #
    def tickets(self) -> list[OrderTicketInternal]:
        """The order's existing tickets with their seat coordinates resolved.

        "Existing" is TICKET_DETAIL_LOADER's doing, not this method's - it
        filters the tickets hop on deleted_at, so a released ticket never
        reaches the collection. Filtering again here would duplicate the rule
        and let the two copies drift.

        Synchronous on purpose: the rows are already in the session from the
        repository's eager load, so there is nothing to await. Walking
        `ticket.event_seat.event_zone` here rather than in the mapper keeps the
        relationship traversal inside the transaction that loaded it.
        """
        return [
            OrderTicketInternal(
                id=ticket.id,
                created_at=ticket.created_at,
                updated_at=ticket.updated_at,
                event_seat_id=ticket.event_seat_id,
                event_zone_id=ticket.event_seat.event_zone_id,
                zone_name=ticket.event_seat.event_zone.name,
                row=ticket.event_seat.row,
                col=ticket.event_seat.col,
                serial_number=ticket.event_seat.serial_number,
                amount=ticket.amount,
            )
            for ticket in self._order.tickets
        ]

    def with_tickets(self) -> OrderWithTicketsInternal:
        """The order plus its resolved tickets - what a detail read returns."""
        return OrderWithTicketsInternal(order=self._order, tickets=self.tickets())

    # ----------------------------------------------------------------------- #
    # Mutations
    #
    # Each one flushes and refreshes: updated_at is generated by the database,
    # so without it the caller would map a stale value into the response.
    # ----------------------------------------------------------------------- #
    async def mark_as_paying(self, payload: OrderPay) -> None:
        """Claims the order for an in-flight payment.

        Written and committed *before* the gateway is called, so a second
        request for the same order finds IN_PROGRESS and is rejected rather
        than charging the customer twice.

        NOTE: payload.payment_method is not persisted - the order table has no
        column for it. Either add one (plus a migration) or drop the field from
        OrderPay; carrying it in the request and silently discarding it is the
        one option that will mislead somebody later.
        """
        self._order.payment_status = PaymentStatus.IN_PROGRESS
        await self._apply(self._order)

    async def mark_as_paid(self) -> None:
        """Settles the order once the gateway has confirmed the charge."""
        self._order.payment_status = PaymentStatus.SUCCESS
        self._order.status = OrderStatus.CONFIRMED
        await self._apply(self._order)

    async def mark_as_payment_fail(self) -> None:
        """Releases the in-flight claim after a failed or abandoned charge.

        status stays PENDING: the order is still active and the customer may
        retry until payment_deadline passes.
        """
        self._order.payment_status = PaymentStatus.FAILED
        await self._apply(self._order)

    async def cancel_order(self) -> None:
        """Cancels the order. The row stays - a cancelled order is still part
        of the customer's history, and the event rules deliberately count it."""
        self._order.status = OrderStatus.CANCELLED
        await self._apply(self._order)

    async def check_and_release_seats(self) -> None:
        """Hands the order's seats back in this transaction. Call it before
        soft_delete_cascade(), which hides the tickets it reads."""
        if self._event is None:
            raise RuntimeError("Releasing seats needs the event locked by load_for_release()")
        await self._event.check_and_release_seats(
            [ticket.event_seat_id for ticket in self._order.tickets]
        )

    async def soft_delete_cascade(self, payload: OrderDelete, manager_id: int | None) -> None:
        """Soft-deletes the order and its tickets.

        Tickets go too, because the partial unique index on
        (order_id, event_seat_id) only ignores soft-deleted rows: leaving them
        in place would keep the seats occupied by an order that no longer exists.

        This is what releases an expired cart. Only soft-deleted orders stop
        blocking an event from being unpublished - see
        EventOrderStatsInternal - so the distinction from cancel_order()
        carries real weight.
        """
        now = now_utc()
        order = self._order
        await self._delete_tickets(now=now, tickets=order.tickets)
        await self._delete_order(now=now, order=order, payload=payload, manager_id=manager_id)
        await self._apply(order)

    @staticmethod
    async def _delete_order(now: datetime, order: Order, payload: OrderDelete, manager_id: int | None):
        order.comment = payload.reason
        order.deleted_at = now
        order.deleted_by_manager_id = manager_id

    @staticmethod
    async def _delete_tickets(now: datetime, tickets: list[OrderTicket]):
        for ticket in tickets:
            if ticket.deleted_at is None:
                ticket.deleted_at = now

    @staticmethod
    async def _add_ticket_release_job(now: datetime, tickets: list[OrderTicket]):
        for ticket in tickets:
            if ticket.deleted_at is None:
                ticket.deleted_at = now

    # ----------------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------------- #
    async def _apply(self, instance) -> None:
        """Flush a mutation and re-read the DB-generated columns."""
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)

    async def _add_release_ticket_jobs(self, tickets: list[OrderTicket]) -> None:
        # TODO: 排程釋放這些票在 Redis 的鎖位
        ...