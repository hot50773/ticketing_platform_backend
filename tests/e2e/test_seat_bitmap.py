"""EventLayoutCache against a real Redis.

The lock and the unlock each log the seats they touched. That pairing is the
only forensic trail there is: if the process dies after a lock, the order was
never written, so no row in Postgres points at those bits either.
"""
import logging

import pytest

from app.cache.event_layout import EventLayoutCache
from app.containers import Container
from app.exceptions import (
    SeatOccupiedError,
    SeatUnexistError,
    UnknownSeatLockFailureError,
)
from app.schemas.event import EventSeatsPricingInternal, SeatPositionInternal, SeatPriceInternal


@pytest.fixture
async def warmed_event(container: Container, seeded_event: dict) -> dict:
    """An event whose existence bitmap is in Redis, so seats can be locked."""
    cache = container.event_layout_cache()
    async with container.event_repository().load(seeded_event["event_id"]) as event:
        layout = await event.event_layout()
    await cache.init_event_layouts(layout)

    return {
        "cache": cache,
        "event_id": seeded_event["event_id"],
        "seats": [
            SeatPriceInternal(
                event_seat_id=seat.id,
                event_zone_id=zone.id,
                seat_serial_number=seat.serial_number,
                price=1500,
            )
            for zone in layout.zones
            for seat in zone.seats
        ],
    }


@pytest.mark.asyncio
async def test_locking_names_the_seats_it_took(warmed_event, caplog):
    """The line that survives a kill -9 between the lock and the insert."""
    with caplog.at_level(logging.INFO, logger="app.cache.event_layout"):
        await warmed_event["cache"].check_and_lock_seats_atomic(
            EventSeatsPricingInternal(
                event_id=warmed_event["event_id"], seats=warmed_event["seats"]
            )
        )

    assert "Locked seats in Redis" in caplog.text
    for seat in warmed_event["seats"]:
        assert str(seat.seat_serial_number) in caplog.text


@pytest.mark.asyncio
async def test_unlocking_names_the_seats_it_freed(warmed_event, caplog):
    cache, event_id, seats = (
        warmed_event["cache"], warmed_event["event_id"], warmed_event["seats"]
    )
    await cache.check_and_lock_seats_atomic(
        EventSeatsPricingInternal(event_id=event_id, seats=seats)
    )

    with caplog.at_level(logging.INFO, logger="app.cache.event_layout"):
        await cache.release_seats(
            event_id=event_id,
            seats=[
                SeatPositionInternal(
                    event_zone_id=s.event_zone_id, serial_number=s.seat_serial_number
                )
                for s in seats
            ],
        )

    assert "Unlocked seats in Redis" in caplog.text


@pytest.mark.asyncio
async def test_a_released_seat_can_be_locked_again(warmed_event):
    """The pair has to actually round-trip, not just log about it."""
    cache, event_id, seats = (
        warmed_event["cache"], warmed_event["event_id"], warmed_event["seats"]
    )
    pricing = EventSeatsPricingInternal(event_id=event_id, seats=seats)

    await cache.check_and_lock_seats_atomic(pricing)
    await cache.release_seats(
        event_id=event_id,
        seats=[
            SeatPositionInternal(
                event_zone_id=s.event_zone_id, serial_number=s.seat_serial_number
            )
            for s in seats
        ],
    )
    await cache.check_and_lock_seats_atomic(pricing)


# --------------------------------------------------------------------------- #
# Why the lock failed
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_seat_that_is_not_on_the_layout_says_so(warmed_event):
    """Reporting this as taken sends the customer back to refresh and retry a
    seat that was never there."""
    phantom = SeatPriceInternal(
        event_seat_id=9999,
        event_zone_id=warmed_event["seats"][0].event_zone_id,
        seat_serial_number=99,
        price=1500,
    )

    with pytest.raises(SeatUnexistError) as exc:
        await warmed_event["cache"].check_and_lock_seats_atomic(
            EventSeatsPricingInternal(event_id=warmed_event["event_id"], seats=[phantom])
        )

    assert "99" in str(exc.value)


@pytest.mark.asyncio
async def test_a_seat_somebody_already_took_says_so(warmed_event):
    cache, event_id, seats = (
        warmed_event["cache"], warmed_event["event_id"], warmed_event["seats"]
    )
    pricing = EventSeatsPricingInternal(event_id=event_id, seats=seats)
    await cache.check_and_lock_seats_atomic(pricing)

    with pytest.raises(SeatOccupiedError):
        await cache.check_and_lock_seats_atomic(pricing)


def test_a_reason_nobody_handles_is_not_guessed_at():
    """The branch that keeps the next failure code from inheriting "already
    taken" by default."""
    with pytest.raises(UnknownSeatLockFailureError):
        EventLayoutCache._raise_lock_failure(
            err_code="SEAT_SOMETHING_NEW", event_id=1, zone_id=2, seat_serial=3
        )
