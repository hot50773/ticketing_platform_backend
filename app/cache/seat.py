import logging
from app.db.redis import RedisClient

logger = logging.getLogger(__name__)

class SeatLockCache:
    """Cache client wrapper managing Redis byte array and single TTL for concert seat reservations."""
    
    LOCK_PREFIX = "seat_lock"
    ORDER_TTL_PREFIX = "order_ttl"
    ZONE_SEATS_PREFIX = "event_zone_seats"

    def __init__(self, redis_client: RedisClient):
        self.redis = redis_client

    async def init_zone_seats(self, event_zone_id: int, size: int, seat_statuses: dict[int, int]) -> None:
        """Initialize the zone's seat status array in Redis using BITFIELD."""
        key = f"{self.ZONE_SEATS_PREFIX}:{event_zone_id}"
        exists = await self.redis.exists(key)
        if not exists:
            last_offset = size - 1
            last_status = seat_statuses.get(last_offset, 0)
            
            cmd = ["BITFIELD", key, "SET", "u8", str(last_offset * 8), str(last_status)]
            for offset, status in seat_statuses.items():
                if offset != last_offset and status != 0:
                    cmd.extend(["SET", "u8", str(offset * 8), str(status)])
            
            await self.redis.execute_command(*cmd)

    async def lock_seats(
        self, event_zone_id: int, seat_offsets: list[int], order_id: int, ttl_seconds: int = 600
    ) -> bool:
        """Atomically lock multiple seats in a zone. Sets status to 0x01 and registers order TTL."""
        key = f"{self.ZONE_SEATS_PREFIX}:{event_zone_id}"
        
        script = """
        local key = KEYS[1]
        local offsets = ARGV
        for _, offset_str in ipairs(offsets) do
            local offset = tonumber(offset_str)
            local bit_offset = offset * 8
            local res = redis.call('BITFIELD', key, 'GET', 'u8', bit_offset)
            local status = res[1] or 0
            if status ~= 0 then
                return {0, offset, status}
            end
        end
        for _, offset_str in ipairs(offsets) do
            local offset = tonumber(offset_str)
            local bit_offset = offset * 8
            redis.call('BITFIELD', key, 'SET', 'u8', bit_offset, 1)
        end
        return {1}
        """
        
        args = [str(offset) for offset in seat_offsets]
        result = await self.redis.eval(script, 1, key, *args)
        
        if result and result[0] == 1:
            return True
            
        logger.warning(f"Lua lock failed details: result={result}, key={key}, offsets={seat_offsets}")
        return False

    async def lock_seat(
        self, event_id: int, event_zone_id: int, seat_offset: int, seat_id: int, order_id: int, ttl_seconds: int = 600
    ) -> bool:
        """Atomically lock seat at offset. Sets status to 0x01 and registers order TTL."""
        return await self.lock_seats(event_zone_id, [seat_offset], order_id, ttl_seconds)

    async def unlock_seats(self, event_zone_id: int, seat_offsets: list[int], order_id: int) -> bool:
        """Unlock multiple seats in a zone by resetting status to 0x00 and removing the TTL key."""
        key = f"{self.ZONE_SEATS_PREFIX}:{event_zone_id}"
        
        script = """
        local key = KEYS[1]
        local offsets = ARGV
        for _, offset_str in ipairs(offsets) do
            local offset = tonumber(offset_str)
            local bit_offset = offset * 8
            local res = redis.call('BITFIELD', key, 'GET', 'u8', bit_offset)
            local status = res[1] or 0
            if status == 1 then
                redis.call('BITFIELD', key, 'SET', 'u8', bit_offset, 0)
            end
        end
        return 1
        """
        
        args = [str(offset) for offset in seat_offsets]
        await self.redis.eval(script, 1, key, *args)
        return True


    async def confirm_seats(self, event_zone_id: int, seat_offsets: list[int]) -> bool:
        """Confirm seat booking permanently by setting status to 0x02."""
        key = f"{self.ZONE_SEATS_PREFIX}:{event_zone_id}"
        
        cmd = ["BITFIELD", key]
        for offset in seat_offsets:
            cmd.extend(["SET", "u8", str(offset * 8), "2"])
            
        await self.redis.execute_command(*cmd)
        return True

    async def get_seats_status(self, event_zone_id: int, seat_offsets: list[int]) -> list[int]:
        """Gets the status values for multiple seats in a zone using BITFIELD GET."""
        if not seat_offsets:
            return []
            
        key = f"{self.ZONE_SEATS_PREFIX}:{event_zone_id}"
        cmd = ["BITFIELD", key]
        for offset in seat_offsets:
            cmd.extend(["GET", "u8", str(offset * 8)])
            
        redis_statuses = await self.redis.execute_command(*cmd)
        return redis_statuses if redis_statuses else []

    async def add_to_delay_queue(self, order_id: int, deadline_timestamp: float) -> None:
        """Adds an order to the Redis ZSET delay queue with its payment deadline as the score."""
        await self.redis.zadd("order_delay_queue", {str(order_id): deadline_timestamp})

    async def remove_from_delay_queue(self, order_id: int) -> None:
        """Removes an order from the Redis ZSET delay queue."""
        await self.redis.zrem("order_delay_queue", str(order_id))

    async def get_expired_orders(self, current_timestamp: float, limit: int = 100) -> list[int]:
        """Fetches up to limit order IDs whose deadlines are less than or equal to current_timestamp."""
        expired_strs = await self.redis.zrangebyscore(
            "order_delay_queue",
            "-inf",
            current_timestamp,
            start=0,
            num=limit
        )
        return [int(oid) for oid in expired_strs] if expired_strs else []



