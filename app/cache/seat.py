import logging
from app.db.redis import RedisClient

logger = logging.getLogger(__name__)

class SeatLockCache:
    """Cache client wrapper managing Redis distributed locks for concert seat reservations."""
    
    LOCK_PREFIX = "seat_lock"

    def __init__(self, redis_client: RedisClient):
        self.redis = redis_client

    def _get_key(self, event_id: int, seat_id: int) -> str:
        return f"{self.LOCK_PREFIX}:{event_id}:{seat_id}"

    async def lock_seat(self, event_id: int, seat_id: int, user_id: int, ttl_seconds: int = 900) -> bool:
        """Attempts to lock a seat for a specific user using SETNX.
        
        Returns:
            True if the lock was successfully acquired, False otherwise.
        """
        key = self._get_key(event_id, seat_id)
        # SET key value NX EX ttl_seconds
        # nx=True: Set the key if it does not exist
        # ex=ttl_seconds: Set the specified expire time in seconds
        success = await self.redis.set(key, str(user_id), nx=True, ex=ttl_seconds)
        if success:
            logger.info(f"Successfully locked seat {seat_id} for event {event_id} to user {user_id} for {ttl_seconds} seconds.")
            return True
        logger.warning(f"Failed to lock seat {seat_id} for event {event_id} to user {user_id} (already locked).")
        return False

    async def unlock_seat(self, event_id: int, seat_id: int, user_id: int) -> bool:
        """Releases a lock on a seat only if it is held by the specified user (atomic).
        
        Returns:
            True if the lock was released, False if the lock didn't exist or belonged to someone else.
        """
        key = self._get_key(event_id, seat_id)
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        result = await self.redis.eval(lua_script, 1, key, str(user_id))
        if result:
            logger.info(f"Released lock on seat {seat_id} for event {event_id} held by user {user_id}.")
            return True
        logger.warning(f"Failed to release lock on seat {seat_id} for event {event_id} (lock mismatch or not found).")
        return False

    async def force_unlock_seat(self, event_id: int, seat_id: int) -> bool:
        """Force releases a lock on a seat.
        
        Returns:
            True if a lock was deleted, False otherwise.
        """
        key = self._get_key(event_id, seat_id)
        result = await self.redis.delete(key)
        if result:
            logger.info(f"Force released lock on seat {seat_id} for event {event_id}.")
            return True
        return False

    async def get_lock_holder(self, event_id: int, seat_id: int) -> int | None:
        """Retrieves the user_id of the user currently holding the seat lock, if any."""
        key = self._get_key(event_id, seat_id)
        val = await self.redis.get(key)
        return int(val) if val is not None else None
