import logging
from app.cache.seat import SeatLockCache
from app.repositories.event import EventRepository

logger = logging.getLogger(__name__)

class EventService:
    def __init__(self, event_repository: EventRepository, event_lock_cache: SeatLockCache):
        self._repository = event_repository
        self.event_lock_cache = event_lock_cache

    async def list_seats(self, event_id: int) -> list[dict]:
        """Retrieves all seats for a given event, returning their ID, layout, and booking status."""
        raise NotImplementedError()

    async def lock_seat(self, event_id: int, seat_id: int, user_id: int) -> bool:
        """Locks a seat in both Redis (for 10 min TTL) and PostgreSQL."""
        raise NotImplementedError()

    async def unlock_seat(self, event_id: int, seat_id: int, user_id: int) -> bool:
        """Unlocks a seat, releasing the Redis lock and resetting PostgreSQL status."""
        raise NotImplementedError()
