from datetime import datetime, timezone
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models._models import Order, OrderTicket
from app.enums import OrderStatus, PaymentStatus


class OrderRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def get_pending_order_by_user(
        self, session: AsyncSession, user_id: int, event_id: int
    ) -> Order | None:
        """Fetches the active (non-deleted, PENDING) order for a user and event."""
        stmt = (
            select(Order)
            .where(Order.user_id == user_id)
            .where(Order.event_id == event_id)
            .where(Order.status == OrderStatus.PENDING.value)
            .where(Order.deleted_at == None)
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_or_create_pending_order(
        self, user_id: int, event_id: int, payment_deadline: datetime
    ) -> int:
        """Gets the active pending order ID or creates a new one in a dedicated transaction."""
        async with self.session_factory() as session:
            stmt = (
                select(Order)
                .where(Order.user_id == user_id)
                .where(Order.event_id == event_id)
                .where(Order.status == OrderStatus.PENDING.value)
                .where(Order.deleted_at == None)
            )
            res = await session.execute(stmt)
            order = res.scalar_one_or_none()
            if not order:
                order = Order(
                    user_id=user_id,
                    event_id=event_id,
                    status=OrderStatus.PENDING.value,
                    payment_status=PaymentStatus.PENDING.value,
                    checkout_amount=0,
                    payment_deadline=payment_deadline
                )
                session.add(order)
                await session.flush()
            order_id = order.id
            await session.commit()
            return order_id


    async def get_order_by_id(self, session: AsyncSession, order_id: int) -> Order | None:
        """Fetches an order by its ID, ignoring soft deletion."""
        stmt = select(Order).where(Order.id == order_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def create_order(
        self, session: AsyncSession, user_id: int, event_id: int, payment_deadline: datetime
    ) -> Order:
        """Creates a new PENDING order for a user and event with a payment deadline."""
        order = Order(
            user_id=user_id,
            event_id=event_id,
            status=OrderStatus.PENDING.value,
            payment_status=PaymentStatus.PENDING.value,
            checkout_amount=0,
            payment_deadline=payment_deadline
        )
        session.add(order)
        await session.flush()

        return order


    async def create_order_ticket(
        self, session: AsyncSession, order_id: int, event_seat_id: int, amount: int
    ) -> OrderTicket:
        """Creates an OrderTicket linking an order and an EventSeat."""
        ticket = OrderTicket(
            order_id=order_id,
            event_seat_id=event_seat_id,
            amount=amount
        )
        session.add(ticket)
        await session.flush()
        return ticket

    async def get_order_tickets(
        self, session: AsyncSession, order_id: int
    ) -> list[OrderTicket]:
        """Retrieves all tickets associated with a given order."""
        stmt = select(OrderTicket).where(OrderTicket.order_id == order_id)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def soft_delete_order(self, session: AsyncSession, order_id: int) -> None:
        """Soft deletes an order by setting its deleted_at field."""
        stmt = select(Order).where(Order.id == order_id).where(Order.deleted_at == None)
        res = await session.execute(stmt)
        order = res.scalar_one_or_none()
        if order:
            order.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
            order.status = OrderStatus.CANCELLED.value
            session.add(order)
            await session.flush()

    async def get_expired_pending_orders(
        self, session: AsyncSession, threshold_time: datetime
    ) -> list[Order]:
        """Fetches all PENDING, non-deleted orders whose payment_deadline has passed."""
        stmt = (
            select(Order)
            .where(Order.status == OrderStatus.PENDING.value)
            .where(Order.payment_deadline < threshold_time)
            .where(Order.deleted_at == None)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())


