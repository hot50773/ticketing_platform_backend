from datetime import datetime, timezone
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models._models import EventSeat, EventZone, Seat, Order, OrderTicket
from app.enums import SeatStatus, OrderStatus, PaymentStatus

from app.schemas import EventSeatDetail



class EventRepository:
    """Repository class managing seat database interactions."""
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def get_event_seat(self, event_id: int, seat_id: int, session: AsyncSession | None = None) -> EventSeat | None:
        """Fetches an EventSeat by event_id and seat_id."""
        stmt = (
            select(EventSeat)
            .join(EventZone, EventSeat.event_zone_id == EventZone.id)
            .where(EventZone.event_id == event_id)
            .where(EventSeat.seat_id == seat_id)
        )
        if session:
            res = await session.execute(stmt)
            return res.scalar_one_or_none()
        async with self.session_factory() as local_session:
            res = await local_session.execute(stmt)
            return res.scalar_one_or_none()

    async def get_event_seat_with_lock(self, session: AsyncSession, event_id: int, seat_id: int) -> EventSeat | None:
        """Fetches an EventSeat with a row-level pessimistic lock (SELECT FOR UPDATE)."""
        stmt = (
            select(EventSeat)
            .join(EventZone, EventSeat.event_zone_id == EventZone.id)
            .where(EventZone.event_id == event_id)
            .where(EventSeat.seat_id == seat_id)
            .with_for_update()
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_event_seats_by_ids(self, event_id: int, seat_ids: list[int], session: AsyncSession | None = None) -> list[EventSeat]:
        """Fetches list of EventSeats by event_id and multiple seat_ids."""
        stmt = (
            select(EventSeat)
            .join(EventZone, EventSeat.event_zone_id == EventZone.id)
            .where(EventZone.event_id == event_id)
            .where(EventSeat.seat_id.in_(seat_ids))
        )
        if session:
            res = await session.execute(stmt)
            return list(res.scalars().all())
        async with self.session_factory() as local_session:
            res = await local_session.execute(stmt)
            return list(res.scalars().all())

    async def get_event_seats_with_lock(self, session: AsyncSession, event_id: int, seat_ids: list[int]) -> list[EventSeat]:
        """Fetches list of EventSeats with a row-level pessimistic lock (SELECT FOR UPDATE)."""
        stmt = (
            select(EventSeat)
            .join(EventZone, EventSeat.event_zone_id == EventZone.id)
            .where(EventZone.event_id == event_id)
            .where(EventSeat.seat_id.in_(seat_ids))
            .with_for_update()
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def update_seat_status(self, event_seat_id: int, status: SeatStatus, session: AsyncSession | None = None) -> EventSeat | None:
        """Updates the status of a specific EventSeat."""
        if session:
            stmt = select(EventSeat).where(EventSeat.id == event_seat_id)
            res = await session.execute(stmt)
            event_seat = res.scalar_one_or_none()
            if event_seat:
                event_seat.status = status
                session.add(event_seat)
                await session.flush()
            return event_seat
        async with self.session_factory() as local_session:
            stmt = select(EventSeat).where(EventSeat.id == event_seat_id)
            res = await local_session.execute(stmt)
            event_seat = res.scalar_one_or_none()
            if event_seat:
                event_seat.status = status
                local_session.add(event_seat)
                await local_session.commit()
            return event_seat

    async def update_seats_status(self, session: AsyncSession, event_seat_ids: list[int], status: SeatStatus) -> None:
        """Updates the status of multiple EventSeats within a session."""
        stmt = select(EventSeat).where(EventSeat.id.in_(event_seat_ids))
        res = await session.execute(stmt)
        event_seats = res.scalars().all()
        for es in event_seats:
            es.status = status
            session.add(es)
        await session.flush()

    async def get_all_seats_by_event(self, event_id: int, session: AsyncSession | None = None) -> list[EventSeatDetail]:
        """Retrieves all seats for a given event, including physical seat details."""
        stmt = (
            select(EventSeat, Seat)
            .join(Seat, EventSeat.seat_id == Seat.id)
            .join(EventZone, EventSeat.event_zone_id == EventZone.id)
            .where(EventZone.event_id == event_id)
            .order_by(Seat.zone_id, Seat.serial_number)
        )
        if session:
            res = await session.execute(stmt)
            rows = res.all()
        else:
            async with self.session_factory() as local_session:
                res = await local_session.execute(stmt)
                rows = res.all()
        return [EventSeatDetail(event_seat=es, seat=seat) for es, seat in rows]

    async def get_event_seats_details(self, event_id: int, seat_ids: list[int], session: AsyncSession | None = None) -> list[EventSeatDetail]:
        """Retrieves detailed EventSeatDetail objects containing EventSeat, Seat, and EventZone."""
        stmt = (
            select(EventSeat, Seat, EventZone)
            .join(Seat, EventSeat.seat_id == Seat.id)
            .join(EventZone, EventSeat.event_zone_id == EventZone.id)
            .where(EventZone.event_id == event_id)
            .where(EventSeat.seat_id.in_(seat_ids))
        )
        if session:
            res = await session.execute(stmt)
            rows = res.all()
        else:
            async with self.session_factory() as local_session:
                res = await local_session.execute(stmt)
                rows = res.all()
        return [EventSeatDetail(event_seat=es, seat=seat, event_zone=ez) for es, seat, ez in rows]

    async def get_event_seats_with_seat_details(self, session: AsyncSession, event_seat_ids: list[int]) -> list[EventSeatDetail]:
        """Retrieves EventSeatDetail objects for multiple EventSeat IDs within a transaction."""
        stmt = (
            select(EventSeat, Seat)
            .join(Seat, EventSeat.seat_id == Seat.id)
            .where(EventSeat.id.in_(event_seat_ids))
        )
        res = await session.execute(stmt)
        rows = res.all()
        return [EventSeatDetail(event_seat=es, seat=seat) for es, seat in rows]

    async def get_event_seats_by_ids_with_lock(self, session: AsyncSession, event_seat_ids: list[int]) -> list[EventSeat]:
        """Fetches a list of EventSeats by their IDs with a pessimistic write lock (SELECT FOR UPDATE)."""
        stmt = select(EventSeat).where(EventSeat.id.in_(event_seat_ids)).with_for_update()
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def get_seats_details_by_event_seat_ids(self, event_seat_ids: list[int]) -> list[EventSeatDetail]:
        """Retrieves detailed EventSeatDetail objects for multiple EventSeat IDs without an active session."""
        stmt = (
            select(EventSeat, Seat)
            .join(Seat, EventSeat.seat_id == Seat.id)
            .where(EventSeat.id.in_(event_seat_ids))
        )
        async with self.session_factory() as local_session:
            res = await local_session.execute(stmt)
            rows = res.all()
        return [EventSeatDetail(event_seat=es, seat=seat) for es, seat in rows]


    async def execute_lock_seats_transaction(
        self, order_id: int, event_id: int, seat_ids: list[int], seat_prices: dict[int, int]
    ) -> None:
        """Executes the complete database transaction for locking seats, creating the tickets under an order."""
        async with self.session_factory() as session:
            stmt_seats = (
                select(EventSeat)
                .join(EventZone, EventSeat.event_zone_id == EventZone.id)
                .where(EventZone.event_id == event_id)
                .where(EventSeat.seat_id.in_(seat_ids))
                .with_for_update()
            )
            res_seats = await session.execute(stmt_seats)
            db_event_seats = res_seats.scalars().all()
            
            for es in db_event_seats:
                status_value = es.status.value if hasattr(es.status, "value") else es.status
                if status_value != "RELEASED":
                    raise ValueError("Seat already reserved or locked in DB")
                es.status = SeatStatus.LOCK
                session.add(es)
                
            total_amount_increment = 0
            for es in db_event_seats:
                price = seat_prices.get(es.seat_id, 0)
                ticket = OrderTicket(order_id=order_id, event_seat_id=es.id, amount=price)
                session.add(ticket)
                total_amount_increment += price
                
            stmt_order = select(Order).where(Order.id == order_id).with_for_update()
            res_order = await session.execute(stmt_order)
            order = res_order.scalar_one()
            order.checkout_amount += total_amount_increment
            session.add(order)
            
            await session.commit()


    async def execute_unlock_seats_transaction(
        self, event_id: int, seat_ids: list[int], user_id: int
    ) -> tuple[int, list[int], int]:
        """Executes the database transaction for unlocking seats, returning details for cache rollback."""
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
                raise ValueError("Pending order not found")
                
            stmt_t = select(OrderTicket).where(OrderTicket.order_id == order.id)
            res_t = await session.execute(stmt_t)
            order_tickets = list(res_t.scalars().all())
            event_seat_id_to_ticket = {ot.event_seat_id: ot for ot in order_tickets}
            
            stmt_seats = (
                select(EventSeat)
                .join(EventZone, EventSeat.event_zone_id == EventZone.id)
                .where(EventZone.event_id == event_id)
                .where(EventSeat.seat_id.in_(seat_ids))
                .with_for_update()
            )
            res_seats = await session.execute(stmt_seats)
            db_event_seats = res_seats.scalars().all()
            
            es_ids_to_release = []
            total_amount_decrement = 0
            for es in db_event_seats:
                if es.id not in event_seat_id_to_ticket:
                    raise ValueError("Seat not part of this order")
                es_ids_to_release.append(es.id)
                total_amount_decrement += event_seat_id_to_ticket[es.id].amount
                es.status = SeatStatus.RELEASED
                session.add(es)
                
            for es_id in es_ids_to_release:
                ticket = event_seat_id_to_ticket[es_id]
                await session.delete(ticket)
                
            order.checkout_amount -= total_amount_decrement
            if order.checkout_amount < 0:
                order.checkout_amount = 0
                
            remaining_tickets_count = len(order_tickets) - len(es_ids_to_release)
            if remaining_tickets_count <= 0:
                order.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
                order.status = OrderStatus.CANCELLED.value

                
            session.add(order)
            await session.commit()
            return order.id, es_ids_to_release, remaining_tickets_count

    async def execute_release_order_transaction(self, order_id: int) -> list[int]:
        """Executes the transaction to release and cancel an order due to expiration."""
        async with self.session_factory() as session:
            stmt = (
                select(Order)
                .where(Order.id == order_id)
                .where(Order.status == OrderStatus.PENDING.value)
                .where(Order.deleted_at == None)
            )
            res = await session.execute(stmt)
            order = res.scalar_one_or_none()
            if not order:
                return []
                
            stmt_t = select(OrderTicket).where(OrderTicket.order_id == order_id)
            res_t = await session.execute(stmt_t)
            order_tickets = list(res_t.scalars().all())
            if not order_tickets:
                order.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
                order.status = OrderStatus.CANCELLED.value
                session.add(order)
                await session.commit()
                return []
                
            event_seat_ids = [ot.event_seat_id for ot in order_tickets]
            
            stmt_seats = select(EventSeat).where(EventSeat.id.in_(event_seat_ids)).with_for_update()
            res_seats = await session.execute(stmt_seats)
            db_event_seats = res_seats.scalars().all()
            for es in db_event_seats:
                status_value = es.status.value if hasattr(es.status, "value") else es.status
                if status_value == "LOCK":
                    es.status = SeatStatus.RELEASED
                    session.add(es)
                    
            order.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
            order.status = OrderStatus.CANCELLED.value
            session.add(order)
            
            await session.commit()
            return event_seat_ids

    async def get_expired_order_ids(self, threshold_time: datetime) -> list[int]:
        """Fetches pending order IDs whose payment_deadline has passed."""
        async with self.session_factory() as session:
            stmt = (
                select(Order.id)
                .where(Order.status == OrderStatus.PENDING.value)
                .where(Order.payment_deadline < threshold_time)
                .where(Order.deleted_at == None)
            )
            res = await session.execute(stmt)
            return list(res.scalars().all())






