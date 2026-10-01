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
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
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
    EventSeatLayout,
    EventSeatPriceInternal,
    EventSeatSyncCountsInternal,
    EventStateInternal,
    EventZoneCreate,
    EventZoneInternal,
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

    async def event_zones(self) -> list[EventZoneInternal]:
        """Every live zone's price, used by the publish rule."""
        rows = await self._session.execute(
            select(EventZone)
            .where(EventZone.event_id == self._event.id)
            .where(EventZone.deleted_at == None)
        )
        return [
            EventZoneInternal(id=row.id, name=row.name, price=row.price)
            for row in rows.all()
        ]

    async def event_layout(self) -> EventLayoutInternal:
        """Every live zone with its live seats - the publish-time snapshot.

        Two filters carry weight here. Soft-deleted seats are excluded, or a
        retired seat would be handed a bit in the existence bitmap. And the
        join is an OUTER join, so a zone with no seats still comes back: the
        publish rule prices every zone it is shown, and an empty zone silently
        dropped here would be an unpriced zone that never blocks a publish.
        """
        rows = await self._session.execute(
            select(EventZone, EventSeat)
            .outerjoin(
                EventSeat,
                (EventSeat.event_zone_id == EventZone.id) & (EventSeat.deleted_at == None),
            )
            .where(EventZone.event_id == self._event.id)
            .where(EventZone.deleted_at == None)
            .order_by(EventZone.name, EventSeat.serial_number)
        )

        zones: dict[int, EventZoneWithSeatInternal] = {}
        for zone, seat in rows.all():
            entry = zones.get(zone.id)
            if entry is None:
                entry = EventZoneWithSeatInternal(
                    id=zone.id, name=zone.name, price=zone.price, seats=[]
                )
                zones[zone.id] = entry
            if seat is not None:
                entry.seats.append(
                    EventSeatInternal(
                        id=seat.id,
                        row=seat.row,
                        col=seat.col,
                        status=seat.status,
                        serial_number=seat.serial_number,
                    )
                )

        return EventLayoutInternal(event=self._event, zones=list(zones.values()))

    async def zone_ids(self) -> list[int]:
        """Live zone ids - what the cache needs to build its keys.

        Deliberately not `event_layout()`: that one joins in every seat, and a
        10k-seat event should not drag all of them through Python just to
        produce a handful of Redis keys.

        Selecting the bare primary key is the one column-level projection the
        repo allows (see .claude/hooks/check_column_queries.py): there is no
        projection shape here to drift, and list[int] is a more honest return
        type than a batch of entities loaded only to read one attribute off.
        """
        rows = await self._session.execute(
            select(EventZone.id)
            .where(EventZone.event_id == self._event.id)
            .where(EventZone.deleted_at == None)
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
            # TO_CHECK: 為這裡 select 語句裡面有兩個 ORM，為何不是 select EventSeat LEFT JOIN EventZone?
            select(EventSeat, EventZone)
            .join(EventZone, EventZone.id == EventSeat.event_zone_id)
            .where(EventSeat.id.in_(event_seat_ids))
            .where(EventSeat.deleted_at == None)
            .where(EventZone.event_id == self._event.id)
            .where(EventZone.deleted_at == None)
        )
        return [
            EventSeatPriceInternal(
                event_seat_id=seat.id, event_zone_id=zone.id, price=zone.price
            )
            for seat, zone in rows.all()
        ]

    async def occupied_seat_count(self, event_zone_id: int) -> int:
        await self._get_and_check_zone(event_zone_id)
        total = await self._session.scalar(
            select(func.count())
            .select_from(EventSeat)
            .where(EventSeat.event_zone_id == event_zone_id)
            .where(EventSeat.deleted_at == None)
            .where(EventSeat.status == OCCUPIED_SEAT_STATUS)
        )
        return total or 0

    # ----------------------------------------------------------------------- #
    # Reads
    # ----------------------------------------------------------------------- #
    async def layout(self) -> EventLayoutSummaryInternal:
        """The event with its zones and per-zone seat counts."""
        rows = await self._session.execute(
            select(EventZone, func.count(EventSeat.id))
            .outerjoin(
                EventSeat,
                (EventSeat.event_zone_id == EventZone.id) & (EventSeat.deleted_at == None),
            )
            .where(EventZone.event_id == self._event.id)
            .where(EventZone.deleted_at == None)
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
        """One page of a zone's live seats, ordered by serial_number."""
        await self._get_and_check_zone(event_zone_id)
        stmt = (
            select(EventSeat)
            .where(EventSeat.event_zone_id == event_zone_id)
            .where(EventSeat.deleted_at == None)
            .order_by(EventSeat.serial_number)
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

    async def soft_delete(self) -> None:
        # TO_CHECK: 是否命名為 soft_delete_cascade 比較好？
        """Soft-deletes the event with its zones and seats. Zones and seats go
        out via bulk UPDATE so a 10k-seat event stays one round trip each."""
        now = now_utc()
        live_zone_ids = (
            select(EventZone.id)
            .where(EventZone.event_id == self._event.id)
            .where(EventZone.deleted_at == None)
            .scalar_subquery()
            # TO_CHECK: 需要跟我解釋這個 scalar_subquery 用途
        )
        await self._session.execute(
            update(EventSeat)
            .where(EventSeat.event_zone_id.in_(live_zone_ids))
            .where(EventSeat.deleted_at == None)
            .values(deleted_at=now)
            # TO_CHECK: In 的寫法是否比 left join 的效能還要差。這是否需要全表掃描？O（n）。是否有更好的寫法
        )
        await self._session.execute(
            update(EventZone)
            .where(EventZone.event_id == self._event.id)
            .where(EventZone.deleted_at == None)
            .values(deleted_at=now)
        )
        self._event.deleted_at = now
        await self._apply(self._event)

    async def add_zone(self, payload: EventZoneCreate) -> EventZoneWithSeatCountInternal:
        """Adds a zone and its seats, numbered from 0 in payload order."""
        event_id = self._event.id
        zone = EventZone(event_id=event_id, name=payload.name, price=payload.price)
        self._session.add(zone)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # TO_CHECK: 這個註解是否為冗余註解？
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
        zone.name = payload.name
        zone.price = payload.price
        self._session.add(zone)
        try:
            # TO_CHECK: 為何這裡要額外多一個 flush?
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise EntityConflictError(
                f"Zone name '{payload.name}' already exists in event {event_id}"
            ) from exc
        await self._session.refresh(zone)
        return EventZoneWithSeatCountInternal(
            zone=zone, seat_count=await self._count_live_seats(event_zone_id)
        )

    async def sync_zone_seats(
        self, event_zone_id: int, seats: list[EventSeatLayout]
    ) -> EventSeatSyncCountsInternal:
        # TO_CHECK: 單一職責原則，這個 method 做了很多事情，是否可以拆細。讓每個 method 只負責一件事
        """Syncs a zone's layout, keyed on `(row, col)`: insert new positions,
        soft-delete missing ones, leave survivors untouched.

        No renumbering - a survivor keeps its serial_number, which *is* its
        Redis bit offset. New seats take max(serial_number) + 1 counting
        soft-deleted rows, so a retired offset is never handed to a different
        seat.
        """
        await self._get_and_check_zone(event_zone_id)
        live = (
            await self._session.execute(
                select(EventSeat)
                .where(EventSeat.event_zone_id == event_zone_id)
                .where(EventSeat.deleted_at == None)
            )
        ).scalars().all()

        desired = {(seat.row, seat.col) for seat in seats}
        current = {(seat.row, seat.col): seat for seat in live}

        retired = [seat for position, seat in current.items() if position not in desired]
        added = sorted(desired - current.keys())

        if retired:
            await self._session.execute(
                update(EventSeat)
                .where(EventSeat.id.in_([seat.id for seat in retired]))
                .values(deleted_at=now_utc())
            )

        next_serial = await self._next_serial_number(event_zone_id)
        self._session.add_all([
            EventSeat(
                event_zone_id=event_zone_id,
                row=row,
                col=col,
                serial_number=next_serial + offset,
                status=SeatStatus.AVAILABLE,
            )
            for offset, (row, col) in enumerate(added)
        ])
        await self._session.flush()

        return EventSeatSyncCountsInternal(
            created_count=len(added),
            deleted_count=len(retired),
            seat_count=len(live) - len(retired) + len(added),
        )

    # ----------------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------------- #
    async def _apply(self, instance) -> None:
        """Flush a mutation and re-read the DB-generated columns."""
        self._session.add(instance)
        # TO_CHECK: 請解釋為何這裡需要 flush 和 refresh
        await self._session.flush()
        await self._session.refresh(instance)

    async def _get_and_check_zone(self, event_zone_id: int) -> EventZone:
        """Fetches a zone and asserts it belongs to this event."""
        zone = await self._session.scalar(
            select(EventZone)
            .where(EventZone.id == event_zone_id)
            .where(EventZone.event_id == self._event.id)
            .where(EventZone.deleted_at == None)
        )
        if not zone:
            raise EntityNotFoundError(
                EVENT_ZONE_NOT_FOUND_MSG.format(
                    event_zone_id=event_zone_id, event_id=self._event.id
                )
            )
        return zone

    async def _count_live_seats(self, event_zone_id: int) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(EventSeat)
            .where(EventSeat.event_zone_id == event_zone_id)
            .where(EventSeat.deleted_at == None)
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
