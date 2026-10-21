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
from collections.abc import Iterable
from pathlib import Path

from redis import RedisError, ResponseError

from app.db.redis import RedisClient
from app.enums import RedisBitUnit, RedisSeatExistence, RedisSeatSoldStatus, RedisResultStatus
from app.exceptions import SeatOccupiedError, EntityNotFoundError
from app.schemas.event import (
    EventLayoutInternal,
    EventZoneWithSeatInternal,
    EventSeatsPricingInternal,
    SeatPositionInternal,
)

logger = logging.getLogger(__name__)

# Paired on purpose. If the process is killed between them, these two lines are
# the only record that seats were locked: the order was never written, so there
# is nothing in Postgres to find them by.
SEATS_LOCKED_MSG = "Locked seats in Redis for event %s - zone:serials %s"
SEATS_UNLOCKED_MSG = "Unlocked seats in Redis for event %s - zone:serials %s"

def load_lua_scripts(path: Path, encoding: str = "utf-8") -> str:
    return path.read_text(encoding=encoding)


LUA_BASE_DIR = Path(__file__).parent / "lua"

class EventLayoutCache:
    """Caches seat layouts for event in Redis."""
    LOCK_SEATS_SCRIPT_PATH = LUA_BASE_DIR / "lock_seats.lua"

    SEAT_SOLD_BITMAP_KEY = "seat_sold_bitmap:event:{event_id}:zone:{zone_id}" # 座位是否售出的紀錄表
    SEAT_EXISTENCE_BITMAP_KEY = "seat_existence_bitmap:event:{event_id}:zone:{zone_id}" # 座位是否存在的紀錄表

    def __init__(self, redis_client: RedisClient):
        self._redis_client = redis_client
        #  TODO: lua script 的處理拆分成 lua script handler
        script = load_lua_scripts(self.LOCK_SEATS_SCRIPT_PATH)
        self._lock_seats_script = self._redis_client.register_script(script)

    async def init_event_layouts(self, event_layout: EventLayoutInternal) -> None:
        """Warms every zone's existence bitmap in one round trip."""
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
        """
        key = self.SEAT_EXISTENCE_BITMAP_KEY.format(event_id=event_id, zone_id=zone.id)
        pipeline.delete(key)

        if zone.seats:
            bitfield = pipeline.bitfield(key)
            for seat in zone.seats:
                self._mark_seat_existence(bitfield=bitfield, offset=seat.serial_number)
            bitfield.execute()

    async def clear_event_layouts(self, event_id: int, zone_ids: list[int]) -> None:
        """Drops every bitmap belonging to an event."""
        if zone_ids:
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
            except RedisError as e:
                logger.exception("Failed to clear seat bitmaps for event %s", event_id)
                raise e

    @staticmethod
    def _mark_seat_existence(bitfield, offset: int):
        """serial_number is the bit offset"""
        return bitfield.set(RedisBitUnit.UINT1, offset, RedisSeatExistence.EXIST.value)

    @staticmethod
    def _mark_seat_release(bitfield, offset: int):
        """serial_number is the bit offset"""
        return bitfield.set(RedisBitUnit.UINT1, offset, RedisSeatSoldStatus.NOT_SOLD.value)

    async def check_and_lock_seats_atomic(self, event_seats_pricing: EventSeatsPricingInternal) -> None:
        event_id = event_seats_pricing.event_id

        zone_id_to_seat_serials_map = self.get_zone_id_to_seat_serials_map(event_seats_pricing)

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

            logger.info(SEATS_LOCKED_MSG, event_id, zone_id_to_seat_serials_map)

        except RedisError as e:
            logger.exception("Failed to lock seat bitmaps for event %s", event_id)
            raise e

    @staticmethod
    def _group_serials_by_zone(seats: Iterable[tuple[int, int]]) -> dict[int, list[int]]:
        """(zone_id, serial_number) pairs -> one bitmap target per zone.

        Both bitmap keys are per-zone, so every write path has to fan its seats
        out this way first. Shared so the lock and release paths cannot drift.
        """
        grouped: dict[int, list[int]] = {}
        for zone_id, serial_number in seats:
            grouped.setdefault(zone_id, []).append(serial_number)
        return grouped

    @classmethod
    def get_zone_id_to_seat_serials_map(
        cls, event_seats_pricing: EventSeatsPricingInternal
    ) -> dict[int, list[int]]:
        return cls._group_serials_by_zone(
            (seat.event_zone_id, seat.seat_serial_number)
            for seat in event_seats_pricing.seats
        )

    async def release_seats(self, event_id: int, seats: list[SeatPositionInternal]) -> None:
        """Clears the sold bits of seats nobody holds any more.

        Grouping them per zone is this layer's business.
        """
        if not seats:
            return

        zone_id_to_seat_serials_map = self._group_serials_by_zone(
            (seat.event_zone_id, seat.serial_number) for seat in seats
        )

        try:
            async with self._redis_client.pipeline() as pipeline:
                for zone_id, seat_serials in zone_id_to_seat_serials_map.items():
                    self.mark_seats_release_for_zone(
                        pipeline=pipeline,
                        event_id=event_id,
                        zone_id=zone_id,
                        seat_serials=seat_serials,
                    )
                await pipeline.execute()
            logger.info(SEATS_UNLOCKED_MSG, event_id, zone_id_to_seat_serials_map)
        except RedisError as e:
            logger.exception("Failed to release seats for event %s, zone:seat_serials - %s", event_id, zone_id_to_seat_serials_map)
            raise e

    def mark_seats_release_for_zone(
        self, pipeline, event_id: int, zone_id: int, seat_serials: list[int]
    ) -> None:
        """Buffers one BITFIELD clearing the zone's sold bits. Does no I/O."""
        key = self.SEAT_SOLD_BITMAP_KEY.format(event_id=event_id, zone_id=zone_id)
        bitfield = pipeline.bitfield(key)

        for serial_number in seat_serials:
            self._mark_seat_release(bitfield=bitfield, offset=serial_number)
        bitfield.execute()
