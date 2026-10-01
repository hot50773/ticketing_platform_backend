"""The Event aggregate root: Event plus its zones and seats.

Loaded by EventRepository inside a transaction and holding that session for its
whole lifetime, so a rule checked here cannot go stale before the write that
depends on it. Services never see the session.

The aggregate carries no business rules - it only exposes the state the rules
need (always as `*Internal` DTOs, never ORM rows) and applies the mutations a
use case asks for. Judgement stays in EventRuleValidator.

CONCURRENCY CONTRACT
--------------------
A single transaction is not by itself enough: under READ COMMITTED another
transaction can still insert an order between our count and our update. The
admin paths therefore load the event row with `FOR UPDATE`
(EventRepository.load_for_update).

The booking flow takes `FOR SHARE` on the same row before creating an order
(OrderRepository.load_for_create), which is what lets it read `state` and
`seat_prices()` without an admin changing either underneath. `FOR UPDATE` on
the booking path would serialise every purchase for an event - do not.
"""
from collections.abc import Sequence

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select
from sqlmodel import select

from app.enums import SeatStatus
from app.exceptions import (
    EVENT_ZONE_NOT_FOUND_MSG,
    EntityConflictError,
    EntityNotFoundError,
)
from app.libs.clock.functional import now_utc
from app.models import Event, EventSeat, EventZone, Order
from app.repositories._pagination import paginate
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    EventHoldingTimeUpdate,
    EventLayoutSummaryInternal,
    EventOrderStatsInternal,
    EventProfileUpdate,
    EventSeatDiffInternal,
    EventSeatLayout,
    EventSeatPriceInternal,
    EventSeatSyncCountsInternal,
    EventStateInternal,
    EventZoneCreate,
    EventZoneProfileUpdate,
    EventZoneWithSeatCountInternal,
    EventZoneWithSeatInternal,
    EventLayoutInternal, EventSeatInternal,
)

OCCUPIED_SEAT_STATUS = SeatStatus.RESERVED


class EventAggregate:
    """One event and its layout, bound to the transaction that loaded it."""

    def __init__(self, session: AsyncSession, event: Event):
        self._session = session
        self._event = event

    @property
    def event(self) -> Event:
        """The root row. Read-only as far as callers are concerned - go through
        the mutators so every change is flushed and refreshed consistently."""
        return self._event

    # ----------------------------------------------------------------------- #
    # State for the rules
    # ----------------------------------------------------------------------- #
    @property
    def state(self) -> EventStateInternal:
        return EventStateInternal(
            id=self._event.id,
            is_active=self._event.is_active,
            start_at=self._event.start_at,
        )

    async def event_layout(self) -> EventLayoutInternal:
        """Every zone and seat that still exists - the publish-time snapshot.

        Soft-deleted seats are filtered out as they load, or a retired seat
        would be handed a bit in the existence bitmap.
        """
        rows = await self._session.execute(
            self._zones_with_seats_stmt(self._event.id)
        )
        zones = rows.scalars().all()

        return EventLayoutInternal(
            event=self._event,
            zones=[
                EventZoneWithSeatInternal(
                    id=zone.id,
                    name=zone.name,
                    price=zone.price,
                    seats=[
                        EventSeatInternal(
                            id=seat.id,
                            row=seat.row,
                            col=seat.col,
                            status=seat.status,
                            serial_number=seat.serial_number,
                        )
                        for seat in zone.event_seats
                    ],
                )
                for zone in zones
            ],
        )

    async def zone_ids(self) -> list[int]:
        """Existing zone ids - what the cache needs to build its keys."""
        rows = await self._session.execute(
            self._existing_zones_stmt(self._event.id).with_only_columns(EventZone.id)
        )
        return list(rows.scalars().all())

    async def order_stats(self) -> EventOrderStatsInternal:
        """Counts orders that are not soft-deleted, whatever their status."""
        total = await self._session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.event_id == self._event.id)
            .where(Order.deleted_at == None)
        )
        return EventOrderStatsInternal(active_order_count=total or 0)

    async def seat_prices(self, event_seat_ids: list[int]) -> list[EventSeatPriceInternal]:
        """The named seats of this event, each with its zone's price.

        Takes ids rather than walking the layout: an order prices a handful of
        seats, not 10k.

        Inner join, unlike the rest of this aggregate - a seat that fails to
        resolve must not silently become part of the order. Its absence from
        the result is the signal; OrderValidator turns that into the 404.
        """
        rows = await self._session.execute(
            self._existing_zones_stmt(self._event.id)
            .add_columns(EventSeat)
            .join(EventSeat, EventSeat.event_zone_id == EventZone.id)
            .where(EventSeat.id.in_(event_seat_ids))
            .where(EventSeat.deleted_at == None)
        )
        return [
            EventSeatPriceInternal(
                event_seat_id=seat.id, event_zone_id=zone.id, price=zone.price
            )
            for zone, seat in rows.all()
        ]

    async def occupied_seat_count(self, event_zone_id: int) -> int:
        await self._get_and_check_zone(event_zone_id)
        total = await self._session.scalar(
            self._existing_seats_stmt(event_zone_id)
            .where(EventSeat.status == OCCUPIED_SEAT_STATUS)
            .with_only_columns(func.count())
        )
        return total or 0

    # ----------------------------------------------------------------------- #
    # Reads
    # ----------------------------------------------------------------------- #
    async def layout(self) -> EventLayoutSummaryInternal:
        """The event with its zones and per-zone seat counts."""
        rows = await self._session.execute(
            self._existing_zones_stmt(self._event.id)
            .add_columns(func.count(EventSeat.id))
            .outerjoin(
                EventSeat,
                (EventSeat.event_zone_id == EventZone.id) & (EventSeat.deleted_at == None),
            )
            .group_by(EventZone.id)
            .order_by(EventZone.name)
        )
        return EventLayoutSummaryInternal(
            event=self._event,
            zones=[
                EventZoneWithSeatCountInternal(zone=zone, seat_count=seat_count)
                for zone, seat_count in rows.all()
            ],
        )

    async def zone_seats(
        self, event_zone_id: int, pagination: PaginationParams
    ) -> Page[EventSeat]:
        """One page of a zone's existing seats, ordered by serial_number."""
        await self._get_and_check_zone(event_zone_id)
        stmt = self._existing_seats_stmt(event_zone_id).order_by(
            EventSeat.serial_number
        )
        return await paginate(self._session, stmt, pagination, EventSeat)

    # ----------------------------------------------------------------------- #
    # Mutations
    #
    # Each one flushes and refreshes: updated_at is generated by the database,
    # so without it the caller would map a stale value into the response.
    # ----------------------------------------------------------------------- #
    async def update_profile(self, payload: EventProfileUpdate) -> None:
        self._event.name = payload.name
        self._event.description = payload.description
        await self._apply(self._event)

    async def set_holding_time(self, payload: EventHoldingTimeUpdate) -> None:
        self._event.start_at = payload.start_at
        self._event.end_at = payload.end_at
        await self._apply(self._event)

    async def publish(self) -> None:
        self._event.is_active = True
        await self._apply(self._event)

    async def unpublish(self) -> None:
        self._event.is_active = False
        await self._apply(self._event)

    async def soft_delete_cascade(self) -> list[int]:
        """Soft-deletes the event with its zones and seats; returns the zone
        ids, which no query can find afterwards and the caller needs for Redis.
        RETURNING rather than a subquery, so the seat update keys off data
        instead of statement order.
        """
        now = now_utc()
        zone_ids = list(
            (
                await self._session.execute(
                    update(EventZone)
                    .where(EventZone.event_id == self._event.id)
                    .where(EventZone.deleted_at == None)
                    .values(deleted_at=now)
                    .returning(EventZone.id)
                )
            ).scalars()
        )

        if zone_ids:
            await self._session.execute(
                update(EventSeat)
                .where(EventSeat.event_zone_id.in_(zone_ids))
                .where(EventSeat.deleted_at == None)
                .values(deleted_at=now)
            )

        self._event.deleted_at = now
        await self._apply(self._event)
        return zone_ids

    async def add_zone(self, payload: EventZoneCreate) -> EventZoneWithSeatCountInternal:
        """Adds a zone and its seats, numbered from 0 in payload order."""
        event_id = self._event.id
        zone = EventZone(event_id=event_id, name=payload.name, price=payload.price)
        try:
            self._session.add(zone)
            await self._session.flush()
        except IntegrityError as exc:
            # A failed flush expires every object in the session, so the id has
            # to be captured up front and the transaction wound back before the
            # message is built - otherwise reading it raises PendingRollbackError.
            await self._session.rollback()
            raise EntityConflictError(
                f"Zone name '{payload.name}' already exists in event {event_id}"
            ) from exc

        self._session.add_all([
            EventSeat(
                event_zone_id=zone.id,
                row=seat.row,
                col=seat.col,
                serial_number=serial,
                status=SeatStatus.AVAILABLE,
            )
            for serial, seat in enumerate(payload.seats, start=0)
        ])
        await self._session.flush()
        await self._session.refresh(zone)
        return EventZoneWithSeatCountInternal(zone=zone, seat_count=len(payload.seats))

    async def update_zone_profile(
        self, event_zone_id: int, payload: EventZoneProfileUpdate
    ) -> EventZoneWithSeatCountInternal:
        event_id = self._event.id
        zone = await self._get_and_check_zone(event_zone_id)
        # The write and its one failure mode, together: name collides with
        # another existing zone of this event. flush() is what surfaces it, early
        # enough to translate - at commit time it would escape as a 500.
        try:
            zone.name = payload.name
            zone.price = payload.price
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise EntityConflictError(
                f"Zone name '{payload.name}' already exists in event {event_id}"
            ) from exc
        await self._session.refresh(zone)
        return EventZoneWithSeatCountInternal(
            zone=zone, seat_count=await self._count_existing_seats(event_zone_id)
        )

    async def sync_zone_seats(
        self, event_zone_id: int, seats: list[EventSeatLayout]
    ) -> EventSeatSyncCountsInternal:
        """flow: load what the zone has -> diff against the payload -> apply.

        No renumbering - a survivor keeps its serial_number, which *is* its
        Redis bit offset. New seats take max(serial_number) + 1 counting
        soft-deleted rows, so a retired offset is never handed to a different
        seat.
        """
        await self._get_and_check_zone(event_zone_id)
        existing = (
            await self._session.execute(self._existing_seats_stmt(event_zone_id))
        ).scalars().all()

        diff = self._diff_layout(seats, existing)

        if diff.retired_seat_ids:
            await self._session.execute(
                update(EventSeat)
                .where(EventSeat.id.in_(diff.retired_seat_ids))
                .values(deleted_at=now_utc())
            )

        next_serial = await self._next_serial_number(event_zone_id)
        self._session.add_all([
            EventSeat(
                event_zone_id=event_zone_id,
                row=position.row,
                col=position.col,
                serial_number=next_serial + offset,
                status=SeatStatus.AVAILABLE,
            )
            for offset, position in enumerate(diff.added)
        ])
        await self._session.flush()

        return EventSeatSyncCountsInternal(
            created_count=len(diff.added),
            deleted_count=len(diff.retired_seat_ids),
            seat_count=len(existing) - len(diff.retired_seat_ids) + len(diff.added),
        )

    @staticmethod
    def _diff_layout(
        desired: list[EventSeatLayout], existing: Sequence[EventSeat]
    ) -> EventSeatDiffInternal:
        """Keyed on `(row, col)`; a position in both is left alone, which is
        what preserves its serial_number. `added` is sorted so serial numbers
        come out the same whatever order the client listed the seats in.
        """
        wanted = {(seat.row, seat.col) for seat in desired}
        by_position = {(seat.row, seat.col): seat for seat in existing}
        return EventSeatDiffInternal(
            added=[
                EventSeatLayout(row=row, col=col)
                for row, col in sorted(wanted - by_position.keys())
            ],
            retired_seat_ids=[
                seat.id
                for position, seat in by_position.items()
                if position not in wanted
            ],
        )

    # ----------------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------------- #
    async def _apply(self, instance) -> None:
        """Flush a mutation and re-read the DB-generated columns."""
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)

    async def _get_and_check_zone(self, event_zone_id: int) -> EventZone:
        """Fetches a zone and asserts it belongs to this event."""
        zone = await self._session.scalar(
            self._existing_zones_stmt(self._event.id).where(EventZone.id == event_zone_id)
        )
        if not zone:
            raise EntityNotFoundError(
                EVENT_ZONE_NOT_FOUND_MSG.format(
                    event_zone_id=event_zone_id, event_id=self._event.id
                )
            )
        return zone

    async def _count_existing_seats(self, event_zone_id: int) -> int:
        total = await self._session.scalar(
            self._existing_seats_stmt(event_zone_id).with_only_columns(func.count())
        )
        return total or 0

    async def _next_serial_number(self, event_zone_id: int) -> int:
        """Next free number in a zone, or 0 for a zone with no seats yet.

        Not `(current_max or 0) + 1`: numbering is 0-based, so a zone whose
        only seat is number 0 has a falsy max and would collide with the
        empty-zone case, handing out 1 and leaving bit 0 stranded.
        """
        current_max = await self._session.scalar(
            select(func.max(EventSeat.serial_number)).where(
                EventSeat.event_zone_id == event_zone_id
            )
        )
        return 0 if current_max is None else current_max + 1

    # ----------------------------------------------------------------------- #
    # Statements
    # ----------------------------------------------------------------------- #
    @staticmethod
    def _existing_zones_stmt(event_id: int) -> Select:
        return (
            select(EventZone)
            .where(EventZone.event_id == event_id)
            .where(EventZone.deleted_at == None)
        )

    @staticmethod
    def _existing_seats_stmt(event_zone_id: int) -> Select:
        return (
            select(EventSeat)
            .where(EventSeat.event_zone_id == event_zone_id)
            .where(EventSeat.deleted_at == None)
        )

    @staticmethod
    def _zones_with_seats_stmt(event_id: int) -> Select:
        """Seats arrive in a second SELECT, so a zone with none still comes
        back - the publish rule prices every zone it is shown."""
        return (
            select(EventZone)
            .where(EventZone.event_id == event_id)
            .where(EventZone.deleted_at == None)
            .options(
                selectinload(EventZone.event_seats.and_(EventSeat.deleted_at == None))
            )
            .order_by(EventZone.name)
        )
