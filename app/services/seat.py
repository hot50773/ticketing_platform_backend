from app.cache.nickname import SeatLockCache
from app.repositories.seat import SeatRepository


class SeatService:
    def __init__(self, seat_repository: SeatRepository, seat_lock_cache: SeatLockCache):
        self._repository = seat_repository
        self.seat_lock_cache = seat_lock_cache
