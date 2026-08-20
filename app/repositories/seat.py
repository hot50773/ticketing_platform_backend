from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models._models import EventSeat, EventZone, Seat
from app.enums import SeatStatus

class SeatRepository:
    """Repository class managing seat database interactions."""
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def get_event_seat(self, event_id: int, seat_id: int) -> EventSeat | None:
        """Fetches an EventSeat by event_id and seat_id."""
        async with self.session_factory() as session:
            stmt = (
                select(EventSeat)
                .join(EventZone, EventSeat.event_zone_id == EventZone.id)
                .where(EventZone.event_id == event_id)
                .where(EventSeat.seat_id == seat_id)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_event_seat_with_lock(self, session: AsyncSession, event_id: int, seat_id: int) -> EventSeat | None:
        """Fetches an EventSeat with a row-level pessimistic lock (SELECT FOR UPDATE).
        
        Note:
            Requires an active transaction session passed from the service layer.
        """
        stmt = (
            select(EventSeat)
            .join(EventZone, EventSeat.event_zone_id == EventZone.id)
            .where(EventZone.event_id == event_id)
            .where(EventSeat.seat_id == seat_id)
            .with_for_update()
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_seat_status(self, event_seat_id: int, status: SeatStatus) -> EventSeat | None:
        """Updates the status of a specific EventSeat."""
        async with self.session_factory() as session:
            db_seat = await session.get(EventSeat, event_seat_id)
            if db_seat:
                db_seat.status = status
                session.add(db_seat)
                await session.commit()
                await session.refresh(db_seat)
            return db_seat

    async def get_all_seats_by_event(self, event_id: int) -> list[tuple[EventSeat, Seat]]:
        """Retrieves all seats for a given event, including physical seat details."""
        async with self.session_factory() as session:
            stmt = (
                select(EventSeat, Seat)
                .join(Seat, EventSeat.seat_id == Seat.id)
                .join(EventZone, EventSeat.event_zone_id == EventZone.id)
                .where(EventZone.event_id == event_id)
            )
            result = await session.execute(stmt)
            # return as list of tuples
            return [(row[0], row[1]) for row in result.all()]
