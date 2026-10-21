"""EventAggregate.check_and_release_seats against a real database.

Releasing is a retry path: the background worker re-runs it after a partial
failure, and two seconds of network trouble must not turn into an order whose
seats nobody will ever hand back. So "already AVAILABLE" has to read as success
while a seat that somebody moved behind our back still has to fail loudly.

Driven through the aggregate rather than the API because what is under test is
the SQL - which rows `RETURNING` hands back and which the `WHERE` filters out.
A faked session would assert nothing.
"""
import pytest
from sqlalchemy import update
from sqlmodel import select

from app.containers import Container
from app.enums import SeatStatus
from app.exceptions import SeatReleaseError
from app.libs.clock.functional import now_utc
from app.models import EventSeat, EventZone


@pytest.fixture
async def seat_ids(container: Container, seeded_event: dict) -> list[int]:
    """The event's four cloned seats, ordered so a test can name them by index."""
    async with container.db().get_async_session() as session:
        rows = await session.execute(
            select(EventSeat.id)
            .join(EventZone, EventZone.id == EventSeat.event_zone_id)
            .where(EventZone.event_id == seeded_event["event_id"])
            .order_by(EventSeat.id)
        )
        return list(rows.scalars())


async def _set(container: Container, ids: list[int], **values) -> None:
    async with container.db().get_async_session() as session:
        await session.execute(update(EventSeat).where(EventSeat.id.in_(ids)).values(**values))
        await session.commit()


async def _rows(container: Container, ids: list[int]) -> dict[int, EventSeat]:
    async with container.db().get_async_session() as session:
        found = await session.execute(select(EventSeat).where(EventSeat.id.in_(ids)))
        return {seat.id: seat for seat in found.scalars()}


async def _release(container: Container, event_id: int, ids: list[int]) -> list[int]:
    async with container.event_repository().load_for_update(event_id) as event:
        return await event.check_and_release_seats(ids)


# --------------------------------------------------------------------------- #
# A. The happy path and the repeat
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_reserved_seats_are_released(container, seeded_event, seat_ids):
    await _set(container, seat_ids, status=SeatStatus.RESERVED)

    released = await _release(container, seeded_event["event_id"], seat_ids)

    assert sorted(released) == sorted(seat_ids)
    assert {s.status for s in (await _rows(container, seat_ids)).values()} == {
        SeatStatus.AVAILABLE
    }


@pytest.mark.asyncio
async def test_releasing_already_released_seats_is_not_an_error(
    container, seeded_event, seat_ids
):
    """The worker's second attempt after a partial failure. The seats are
    already where the caller wants them, so there is nothing to report."""
    await _set(container, seat_ids, status=SeatStatus.AVAILABLE)

    released = await _release(container, seeded_event["event_id"], seat_ids)

    assert released == []


@pytest.mark.asyncio
async def test_only_the_seats_this_call_moved_come_back(
    container, seeded_event, seat_ids
):
    already, still_held = seat_ids[:2], seat_ids[2:]
    await _set(container, already, status=SeatStatus.AVAILABLE)
    await _set(container, still_held, status=SeatStatus.RESERVED)

    released = await _release(container, seeded_event["event_id"], seat_ids)

    assert sorted(released) == sorted(still_held)


@pytest.mark.asyncio
async def test_an_already_released_seat_is_not_written_again(
    container, seeded_event, seat_ids
):
    """updated_at is what an operator reads to find out when a seat was handed
    back; a no-op retry must not overwrite that answer."""
    await _set(container, seat_ids, status=SeatStatus.AVAILABLE)
    before = {i: s.updated_at for i, s in (await _rows(container, seat_ids)).items()}

    await _release(container, seeded_event["event_id"], seat_ids)

    assert {i: s.updated_at for i, s in (await _rows(container, seat_ids)).items()} == before


# --------------------------------------------------------------------------- #
# B. States that must still fail
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_soft_deleted_seat_is_rejected(container, seeded_event, seat_ids):
    """A seat retired out from under an order that still holds it. Idempotence
    is for repeats, not for data nobody can explain."""
    await _set(container, seat_ids, status=SeatStatus.RESERVED)
    await _set(container, seat_ids[:1], deleted_at=now_utc())

    with pytest.raises(SeatReleaseError) as exc:
        await _release(container, seeded_event["event_id"], seat_ids)

    assert str(seat_ids[0]) in str(exc.value)


@pytest.mark.asyncio
async def test_unknown_seat_id_is_rejected(container, seeded_event, seat_ids):
    missing = max(seat_ids) + 1000

    with pytest.raises(SeatReleaseError) as exc:
        await _release(container, seeded_event["event_id"], seat_ids + [missing])

    assert str(missing) in str(exc.value)


@pytest.mark.asyncio
async def test_a_bad_seat_outranks_an_already_released_one(
    container, seeded_event, seat_ids
):
    """Both land in the same leftover set. The anomaly has to win, or a batch
    that happens to contain one repeat would swallow it."""
    await _set(container, seat_ids, status=SeatStatus.AVAILABLE)
    await _set(container, seat_ids[:1], deleted_at=now_utc())

    with pytest.raises(SeatReleaseError):
        await _release(container, seeded_event["event_id"], seat_ids)
