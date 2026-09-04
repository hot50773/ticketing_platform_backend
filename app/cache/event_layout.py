"""Redis bitmaps derived from a frozen event layout.

Written at publish time, when the zone/seat layout can no longer change and
`serial_number` is therefore a stable bit offset.

The bitmaps are derived data, never a source of truth: everything here can be
rebuilt from Postgres. What must never happen is a *partially* written bitmap,
which would mark real seats as non-existent - hence one MULTI/EXEC for the
whole event rather than a write per zone.

Note what MULTI/EXEC does and does not buy: no other client can interleave
between our commands, and a malformed command aborts the batch before any of
it runs. It is NOT a rollback - Redis never undoes a command that fails at
runtime. The real guard against a bad bitmap going live is the caller: any
error propagates out of the publish transaction, so the event stays
unpublished and the next publish rewrites the bitmap from scratch.
"""
import logging

from redis import RedisError

from app.db.redis import RedisClient
from app.enums import RedisBitUnit, RedisSeatExistence
from app.schemas.event import EventLayoutInternal, EventZoneWithSeatInternal

logger = logging.getLogger(__name__)


class EventLayoutCache:
    """Caches event layouts in Redis."""

    SEAT_SOLD_BITMAP_KEY = "seat_sold_bitmap:event:{event_id}:zone:{zone_id}" # 座位是否售出的紀錄表
    SEAT_EXISTENCE_BITMAP_KEY = "seat_existence_bitmap:event:{event_id}:zone:{zone_id}" # 座位是否存在的紀錄表

    def __init__(self, redis_client: RedisClient):
        self._redis_client = redis_client

    async def init_event_layouts(self, event_layout: EventLayoutInternal) -> None:
        """Warms every zone's existence bitmap in one round trip.

        redis-py pipelines are MULTI/EXEC by default (verified: `transaction`
        defaults to True); do not pass `transaction=False` here, or another
        client could read a half-built bitmap mid-write.

        Raises rather than swallowing: the caller runs this inside the publish
        transaction, so a dead Redis unwinds the publish instead of putting an
        event on sale with no bitmap behind it.
        """
        event_id = event_layout.event.id
        try:
            async with self._redis_client.pipeline() as pipeline:
                for zone in event_layout.zones:
                    self.mark_seats_existence_for_zone(
                        pipeline=pipeline, event_id=event_id, zone=zone
                    )
                await pipeline.execute()
        except RedisError:
            logger.exception("Failed to warm seat bitmaps for event %s", event_id)
            raise

    def mark_seats_existence_for_zone(
        self, pipeline, event_id: int, zone: EventZoneWithSeatInternal
    ) -> None:
        """Buffers one BITFIELD for the zone. Does no I/O - the caller's
        `pipeline.execute()` is what sends it.

        `BitFieldOperation.set()` only accumulates sub-operations in memory;
        `.execute()` is what renders them into a BITFIELD command and queues it
        on the pipeline. Miss that call and the pipeline goes out empty.
        """
        if not zone.seats:
            return

        key = self.SEAT_EXISTENCE_BITMAP_KEY.format(event_id=event_id, zone_id=zone.id)
        bitfield = pipeline.bitfield(key)
        for seat in zone.seats:
            self._mark_seat_existence(bitfield=bitfield, offset=seat.serial_number - 1)
        bitfield.execute()

    @staticmethod
    def _mark_seat_existence(bitfield, offset: int):
        """offset = serial_number - 1, per the contract in app/schemas/event.py.
        TODO: the -1 disappears once serial_number is migrated to 0-based.

        `.value` is not decoration: redis-py encodes ints with `repr()`, which
        turns a bare IntEnum member into `<RedisSeatExistence.EXIST: 1>` and
        earns a "value is not an integer" from Redis. RedisBitUnit is a StrEnum
        and *is* a str, so it needs no unwrapping.
        """
        return bitfield.set(RedisBitUnit.UINT1, offset, RedisSeatExistence.EXIST.value)
