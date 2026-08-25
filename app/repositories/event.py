from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models._models import EventSeat, EventZone, Seat
from app.enums import SeatStatus

class EventRepository:
    """Repository class managing seat database interactions."""
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def get_event_seat(self, event_id: int, seat_id: int) -> EventSeat | None:
        """Fetches an EventSeat by event_id and seat_id."""
        raise NotImplementedError()

    async def get_event_seat_with_lock(self, session: AsyncSession, event_id: int, seat_id: int) -> EventSeat | None:
        """Fetches an EventSeat with a row-level pessimistic lock (SELECT FOR UPDATE)."""
        raise NotImplementedError()

    async def update_seat_status(self, event_seat_id: int, status: SeatStatus) -> EventSeat | None:
        """Updates the status of a specific EventSeat."""
        raise NotImplementedError()

    async def get_all_seats_by_event(self, event_id: int) -> list[tuple[EventSeat, Seat]]:
        """Retrieves all seats for a given event, including physical seat details."""
        raise NotImplementedError()
