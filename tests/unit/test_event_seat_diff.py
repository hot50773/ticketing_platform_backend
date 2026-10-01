"""Unit tests for EventAggregate._diff_layout.

Pure set arithmetic on `(row, col)`, so it runs without a database - which is
the reason it was pulled out of sync_zone_seats in the first place.
"""
from app.aggregates.event import EventAggregate
from app.enums import SeatStatus
from app.models import EventSeat
from app.schemas.event import EventSeatLayout

ZONE_ID = 10


def seat(seat_id: int, row: int, col: int, serial_number: int = 0) -> EventSeat:
    return EventSeat(
        id=seat_id,
        event_zone_id=ZONE_ID,
        row=row,
        col=col,
        serial_number=serial_number,
        status=SeatStatus.AVAILABLE,
        deleted_at=None,
        source_seat_id=None,
    )


def wanted(*positions: tuple[int, int]) -> list[EventSeatLayout]:
    return [EventSeatLayout(row=row, col=col) for row, col in positions]


def diff(desired, existing):
    return EventAggregate._diff_layout(desired, existing)


def test_new_positions_are_added():
    result = diff(wanted((1, 1), (1, 2)), [seat(100, 1, 1)])

    assert [(p.row, p.col) for p in result.added] == [(1, 2)]
    assert result.retired_seat_ids == []


def test_missing_positions_are_retired():
    result = diff(wanted((1, 1)), [seat(100, 1, 1), seat(101, 1, 2)])

    assert result.added == []
    assert result.retired_seat_ids == [101]


def test_a_position_present_in_both_is_left_alone():
    """Survivors appear in neither list - that is what protects their
    serial_number, and therefore their Redis bit offset."""
    result = diff(wanted((1, 1)), [seat(100, 1, 1, serial_number=7)])

    assert result.added == []
    assert result.retired_seat_ids == []


def test_added_is_sorted_whatever_order_the_client_sent():
    """Serial numbers are handed out by position in `added`, so the ordering
    has to come from the data, not from the payload."""
    scrambled = diff(wanted((2, 2), (1, 3), (1, 1)), [])
    ordered = diff(wanted((1, 1), (1, 3), (2, 2)), [])

    positions = [(p.row, p.col) for p in scrambled.added]
    assert positions == [(1, 1), (1, 3), (2, 2)]
    assert positions == [(p.row, p.col) for p in ordered.added]


def test_an_empty_zone_adds_everything():
    result = diff(wanted((1, 1), (1, 2)), [])

    assert [(p.row, p.col) for p in result.added] == [(1, 1), (1, 2)]
    assert result.retired_seat_ids == []


def test_an_empty_payload_retires_everything():
    result = diff([], [seat(100, 1, 1), seat(101, 1, 2)])

    assert result.added == []
    assert sorted(result.retired_seat_ids) == [100, 101]


def test_a_full_swap_adds_and_retires_in_one_pass():
    result = diff(wanted((9, 9)), [seat(100, 1, 1)])

    assert [(p.row, p.col) for p in result.added] == [(9, 9)]
    assert result.retired_seat_ids == [100]
