"""Redis bitmaps derived from a frozen event layout.

Written at publish time, when the zone/seat layout can no longer change and
`serial_number` is therefore a stable bit offset (0-based, so the column value
is the bit position outright).

Only the existence bitmap is rebuilt on publish. The sold bitmap is deliberately
left alone: publishing an already-published event is not rejected today, and
wiping sold seats out from under a live event would be unrecoverable.

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
import json
import logging

from redis import RedisError, ResponseError

from app.db.redis import RedisClient
from app.enums import RedisBitUnit, RedisSeatExistence, RedisSeatSoldStatus, RedisResultStatus
from app.exceptions import SeatOccupiedError, EntityNotFoundError
from app.schemas.event import (
    EventLayoutInternal,
    EventZoneWithSeatInternal,
    EventSeatsPricingInternal,
)

logger = logging.getLogger(__name__)


class EventLayoutCache:
    """Caches event layouts in Redis."""
    LOCK_SEATS_SCRIPT_PATH = "lua/lock_seats.lua"

    SEAT_SOLD_BITMAP_KEY = "seat_sold_bitmap:event:{event_id}:zone:{zone_id}" # 座位是否售出的紀錄表
    SEAT_EXISTENCE_BITMAP_KEY = "seat_existence_bitmap:event:{event_id}:zone:{zone_id}" # 座位是否存在的紀錄表

    def __init__(self, redis_client: RedisClient):
        self._redis_client = redis_client
        self._lock_seats_script = self._redis_client.register_script(self.LOCK_SEATS_SCRIPT_PATH)

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
        key = self.SEAT_EXISTENCE_BITMAP_KEY.format(event_id=event_id, zone_id=zone.id)

        # DEL first, in the same MULTI as the rebuild: BITFIELD only ever sets
        # bits, so writing over a previous generation of the bitmap would leave
        # a retired seat's bit standing and resurrect a seat that no longer
        # exists. Seats can be soft-deleted and re-added for as long as the
        # event stays unpublished, so this is a live hazard, not a hypothetical.
        pipeline.delete(key)
        if not zone.seats:
            return

        bitfield = pipeline.bitfield(key)
        for seat in zone.seats:
            self._mark_seat_existence(bitfield=bitfield, offset=seat.serial_number)
        bitfield.execute()

    async def clear_event_layouts(self, event_id: int, zone_ids: list[int]) -> None:
        """Drops every bitmap belonging to an event.

        Called *after* the unpublish / delete has committed, which is the
        mirror image of the publish ordering and for the same reason: dropping
        a live event's bitmap would make every seat read as non-existent, while
        a bitmap left behind on an event that is no longer on sale is inert and
        gets rebuilt from scratch by the next publish.
        """
        if not zone_ids:
            return

        try:
            async with self._redis_client.pipeline() as pipeline:
                for zone_id in zone_ids:
                    pipeline.delete(
                        self.SEAT_EXISTENCE_BITMAP_KEY.format(
                            event_id=event_id, zone_id=zone_id
                        ),
                        self.SEAT_SOLD_BITMAP_KEY.format(
                            event_id=event_id, zone_id=zone_id
                        ),
                    )
                await pipeline.execute()
        except RedisError:
            logger.exception("Failed to clear seat bitmaps for event %s", event_id)
            raise

    @staticmethod
    def _mark_seat_existence(bitfield, offset: int):
        """serial_number is the bit offset outright - it is 0-based since the
        7c4e91ab2d38 migration, so there is no arithmetic here to get wrong.

        `.value` is not decoration: redis-py encodes ints with `repr()`, which
        turns a bare IntEnum member into `<RedisSeatExistence.EXIST: 1>` and
        earns a "value is not an integer" from Redis. RedisBitUnit is a StrEnum
        and *is* a str, so it needs no unwrapping.
        """
        return bitfield.set(RedisBitUnit.UINT1, offset, RedisSeatExistence.EXIST.value)

    async def check_and_lock_seats_atomic(self, event_seats_pricing: EventSeatsPricingInternal) -> None:
        self._lock_seats_script = self._redis_client.register_script(self.LOCK_SEATS_SCRIPT_PATH)

        event_id = event_seats_pricing.event_id
        zone_id_to_seat_serials_map = {}

        for seat in event_seats_pricing.seats:
            zone_id = seat.event_zone_id
            seat_serials = zone_id_to_seat_serials_map.get(zone_id, set())
            zone_id_to_seat_serials_map[zone_id] = seat_serials.add(seat.seat_serial_number)

        serialized_seats = json.dumps(zone_id_to_seat_serials_map)
        try:
            result = await self._lock_seats_script(keys=[], args=[event_id, serialized_seats])
            if result[0] == RedisResultStatus.FAILED:
                err_code, err_event_id, err_zone_id, err_seat_serial = result[1:]
                err_msg = "Seat Serial {seat_serial} in Event #{event_id} Zone #{zone_id} is occupied.".format(
                    seat_serial=err_seat_serial,
                    event_id=err_event_id,
                    zone_id=err_zone_id,
                )
                raise SeatOccupiedError(err_msg)

        except RedisError as e:
            logger.exception("Failed to lock seat bitmaps for event %s", event_id)
            raise e
