"""Persistence for the Event aggregate (Event / EventZone / EventSeat).

Every public method owns its own transaction. Soft-deleted rows
(`deleted_at IS NOT NULL`) are excluded from every read.
"""
from sqlalchemy import cast, func, insert, literal, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.enums import SeatStatus
from app.exceptions import (
    EVENT_NOT_FOUND_MSG,
    EVENT_ZONE_NOT_FOUND_MSG,
    VENUE_NOT_FOUND_MSG,
    EntityConflictError,
    EntityNotFoundError,
)
from app.libs.clock.functional import now_utc
from app.models import Event, EventSeat, EventZone, Order, Seat, Venue, Zone
from app.repositories._pagination import paginate
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    EventCreate,
    EventHoldingTimeUpdate,
    EventLayoutInternal,
    EventOrderStatsInternal,
    EventProfileUpdate,
    EventSeatLayout,
    EventSeatSyncCountsInternal,
    EventZoneCreate,
    EventZonePricingInternal,
    EventZoneProfileUpdate,
    EventZoneWithSeatCountInternal,
)

OCCUPIED_SEAT_STATUSES = (SeatStatus.LOCKED, SeatStatus.RESERVED)


class EventRepository:
    """Repository managing Event aggregate database transactions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    # ----------------------------------------------------------------------- #
    # Event
    # ----------------------------------------------------------------------- #
    async def create_event_from_venue_template(self, payload: EventCreate) -> EventLayoutInternal:
        """Creates an unpublished event and clones the venue's layout into
        EventZone/EventSeat inside a single transaction.

        EventSeat.serial_number is copied straight from Seat.serial_number, so
        a zone's numbering can have holes where template seats were deleted.
        Cloning uses `INSERT ... SELECT` rather than ORM row-by-row inserts: a
        10k-seat venue would otherwise round-trip 10k objects through Python.
        Reading zone/seat here crosses aggregates on purpose - it is the only
        way to keep the copy atomic and cheap.

        Raises EntityNotFoundError when the venue does not exist or is deleted.
        """
        async with self.session_factory() as session:
            await self._check_venue(session, payload.venue_id)

            event = Event(
                name=payload.name,
                description=payload.description,
                start_at=payload.start_at,
                end_at=payload.end_at,
                is_active=False,
                venue_id=payload.venue_id,
            )
            session.add(event)
            await session.flush()

            await self._clone_zones(session, event.id, payload.venue_id)
            await self._clone_seats(session, event.id)
            await session.commit()

            await session.refresh(event)
            return await self._read_layout(session, event)

    @staticmethod
    async def _clone_zones(session: AsyncSession, event_id: int, venue_id: int) -> None:
        """Copies the venue's live zones. price stays NULL (not configured yet)."""
        source = (
            select(func.now(), func.now(), literal(event_id), Zone.name, Zone.id)
            .where(Zone.venue_id == venue_id)
            .where(Zone.deleted_at == None)
        )
        await session.execute(
            insert(EventZone).from_select(
                ["created_at", "updated_at", "event_id", "name", "source_zone_id"],
                source,
                include_defaults=False,
            )
        )

    @staticmethod
    async def _clone_seats(session: AsyncSession, event_id: int) -> None:
        """Copies the live template seats of every cloned zone in one statement,
        joining freshly inserted event_zone rows back to their source zone."""
        source = (
            select(
                func.now(),
                func.now(),
                EventZone.id,
                Seat.row,
                Seat.col,
                Seat.serial_number,
                cast(literal(SeatStatus.AVAILABLE.value), EventSeat.__table__.c.status.type),
                Seat.id,
            )
            .select_from(Seat)
            .join(EventZone, EventZone.source_zone_id == Seat.zone_id)
            .where(EventZone.event_id == event_id)
            .where(EventZone.deleted_at == None)
            .where(Seat.deleted_at == None)
        )
        await session.execute(
            insert(EventSeat).from_select(
                [
                    "created_at",
                    "updated_at",
                    "event_zone_id",
                    "row",
                    "col",
                    "serial_number",
                    "status",
                    "source_seat_id",
                ],
                source,
                include_defaults=False,
            )
        )

    async def get_events(self, pagination: PaginationParams) -> Page[Event]:
        """One page of non-deleted events ordered by id, plus the total count."""
        async with self.session_factory() as session:
            stmt = select(Event).where(Event.deleted_at == None).order_by(Event.id)
            return await paginate(session, stmt, pagination, Event)

    async def get_and_check_event(self, event_id: int) -> Event:
        """Fetches a non-deleted event. Raises EntityNotFoundError if absent."""
        async with self.session_factory() as session:
            return await self._get_and_check_event(session, event_id)

    async def get_event_layout(self, event_id: int) -> EventLayoutInternal:
        """Fetches an event with its zones and per-zone seat counts."""
        async with self.session_factory() as session:
            event = await self._get_and_check_event(session, event_id)
            return await self._read_layout(session, event)

    async def update_event_profile(self, event_id: int, payload: EventProfileUpdate) -> Event:
        """Updates name and description."""
        async with self.session_factory() as session:
            event = await self._get_and_check_event(session, event_id)
            event.name = payload.name
            event.description = payload.description
            session.add(event)
            await session.commit()
            await session.refresh(event)
            return event

    async def update_event_holding_time(
        self, event_id: int, payload: EventHoldingTimeUpdate
    ) -> Event:
        """Updates start_at and end_at."""
        async with self.session_factory() as session:
            event = await self._get_and_check_event(session, event_id)
            event.start_at = payload.start_at
            event.end_at = payload.end_at
            session.add(event)
            await session.commit()
            await session.refresh(event)
            return event

    async def update_event_status(self, event_id: int, is_active: bool) -> Event:
        """Publishes or unpublishes the event."""
        async with self.session_factory() as session:
            event = await self._get_and_check_event(session, event_id)
            event.is_active = is_active
            session.add(event)
            await session.commit()
            await session.refresh(event)
            return event

    async def soft_delete_event_cascade(self, event_id: int) -> None:
        """Soft-deletes the event together with its zones and seats in one
        transaction. Zones and seats go out via bulk UPDATE, not row by row."""
        async with self.session_factory() as session:
            event = await self._get_and_check_event(session, event_id)
            now = now_utc()

            live_zone_ids = (
                select(EventZone.id)
                .where(EventZone.event_id == event_id)
                .where(EventZone.deleted_at == None)
                .scalar_subquery()
            )
            await session.execute(
                update(EventSeat)
                .where(EventSeat.event_zone_id.in_(live_zone_ids))
                .where(EventSeat.deleted_at == None)
                .values(deleted_at=now)
            )
            await session.execute(
                update(EventZone)
                .where(EventZone.event_id == event_id)
                .where(EventZone.deleted_at == None)
                .values(deleted_at=now)
            )
            event.deleted_at = now
            session.add(event)
            await session.commit()

    # ----------------------------------------------------------------------- #
    # EventZone / EventSeat
    # ----------------------------------------------------------------------- #
    async def create_event_zone_with_seats(
        self, event_id: int, payload: EventZoneCreate
    ) -> EventZoneWithSeatCountInternal:
        """Creates a zone and its seats atomically, numbering them from 1 in
        payload order. Raises EntityConflictError on a duplicate zone name.
        """
        async with self.session_factory() as session:
            zone = EventZone(event_id=event_id, name=payload.name, price=payload.price)
            session.add(zone)
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                raise EntityConflictError(
                    f"Zone name '{payload.name}' already exists in event {event_id}"
                ) from exc

            session.add_all([
                EventSeat(
                    event_zone_id=zone.id,
                    row=seat.row,
                    col=seat.col,
                    serial_number=serial,
                    status=SeatStatus.AVAILABLE,
                )
                for serial, seat in enumerate(payload.seats, start=1)
            ])
            await session.commit()
            await session.refresh(zone)
            return EventZoneWithSeatCountInternal(zone=zone, seat_count=len(payload.seats))

    async def get_and_check_event_zone(self, event_id: int, event_zone_id: int) -> EventZone:
        """Fetches a zone and asserts it belongs to the given event."""
        async with self.session_factory() as session:
            return await self._get_and_check_event_zone(session, event_id, event_zone_id)

    async def get_event_zone_seats(
        self, event_zone_id: int, pagination: PaginationParams
    ) -> Page[EventSeat]:
        """One page of a zone's non-deleted seats ordered by serial_number."""
        async with self.session_factory() as session:
            stmt = (
                select(EventSeat)
                .where(EventSeat.event_zone_id == event_zone_id)
                .where(EventSeat.deleted_at == None)
                .order_by(EventSeat.serial_number)
            )
            return await paginate(session, stmt, pagination, EventSeat)

    async def update_event_zone_profile(
        self, event_zone_id: int, payload: EventZoneProfileUpdate
    ) -> EventZoneWithSeatCountInternal:
        """Updates a zone's name and ticket price."""
        async with self.session_factory() as session:
            zone = await self._get_live_zone(session, event_zone_id)
            zone.name = payload.name
            zone.price = payload.price
            session.add(zone)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise EntityConflictError(
                    f"Zone name '{payload.name}' already exists in event {zone.event_id}"
                ) from exc
            await session.refresh(zone)
            seat_count = await self._count_live_seats(session, event_zone_id)
            return EventZoneWithSeatCountInternal(zone=zone, seat_count=seat_count)

    async def replace_event_zone_seats(
        self, event_zone_id: int, seats: list[EventSeatLayout]
    ) -> EventSeatSyncCountsInternal:
        """Syncs a zone's layout to `seats` in one transaction, keyed on
        `(row, col)`: insert new positions, soft-delete missing ones, leave
        surviving seats untouched.

        No renumbering: a surviving seat keeps its serial_number (= its Redis
        offset). New seats take max(serial_number) + 1, counting soft-deleted
        rows so a retired offset is never recycled.
        """
        async with self.session_factory() as session:
            live = (
                await session.execute(
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
                now = now_utc()
                await session.execute(
                    update(EventSeat)
                    .where(EventSeat.id.in_([seat.id for seat in retired]))
                    .values(deleted_at=now)
                )

            next_serial = await self._next_serial_number(session, event_zone_id)
            session.add_all([
                EventSeat(
                    event_zone_id=event_zone_id,
                    row=row,
                    col=col,
                    serial_number=next_serial + offset,
                    status=SeatStatus.AVAILABLE,
                )
                for offset, (row, col) in enumerate(added)
            ])
            await session.commit()

            return EventSeatSyncCountsInternal(
                created_count=len(added),
                deleted_count=len(retired),
                seat_count=len(live) - len(retired) + len(added),
            )

    # ----------------------------------------------------------------------- #
    # Rule inputs
    # ----------------------------------------------------------------------- #
    async def get_event_zone_pricings(self, event_id: int) -> list[EventZonePricingInternal]:
        """Every zone's price, used by the publish rule."""
        async with self.session_factory() as session:
            rows = await session.execute(
                select(EventZone.id, EventZone.name, EventZone.price)
                .where(EventZone.event_id == event_id)
                .where(EventZone.deleted_at == None)
                .order_by(EventZone.name)
            )
            return [
                EventZonePricingInternal(id=zone_id, name=name, price=price)
                for zone_id, name, price in rows.all()
            ]

    async def get_event_order_stats(self, event_id: int) -> EventOrderStatsInternal:
        """Counts the event's orders that are not soft-deleted (any status),
        used by the unpublish and delete rules.
        """
        async with self.session_factory() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(Order)
                .where(Order.event_id == event_id)
                .where(Order.deleted_at == None)
            )
            return EventOrderStatsInternal(active_order_count=total or 0)

    async def count_occupied_seats_in_zone(self, event_zone_id: int) -> int:
        """Counts LOCKED / RESERVED seats in the zone, so a layout rewrite
        cannot silently drop a seat somebody is holding.
        """
        async with self.session_factory() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(EventSeat)
                .where(EventSeat.event_zone_id == event_zone_id)
                .where(EventSeat.deleted_at == None)
                .where(EventSeat.status.in_(OCCUPIED_SEAT_STATUSES))
            )
            return total or 0

    # ----------------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------------- #
    @staticmethod
    async def _check_venue(session: AsyncSession, venue_id: int) -> None:
        venue = await session.scalar(
            select(Venue).where(Venue.id == venue_id).where(Venue.deleted_at == None)
        )
        if not venue:
            raise EntityNotFoundError(VENUE_NOT_FOUND_MSG.format(venue_id=venue_id))

    @staticmethod
    async def _get_and_check_event(session: AsyncSession, event_id: int) -> Event:
        event = await session.scalar(
            select(Event).where(Event.id == event_id).where(Event.deleted_at == None)
        )
        if not event:
            raise EntityNotFoundError(EVENT_NOT_FOUND_MSG.format(event_id=event_id))
        return event

    @staticmethod
    async def _get_and_check_event_zone(
        session: AsyncSession, event_id: int, event_zone_id: int
    ) -> EventZone:
        zone = await session.scalar(
            select(EventZone)
            .where(EventZone.id == event_zone_id)
            .where(EventZone.event_id == event_id)
            .where(EventZone.deleted_at == None)
        )
        if not zone:
            raise EntityNotFoundError(
                EVENT_ZONE_NOT_FOUND_MSG.format(event_zone_id=event_zone_id, event_id=event_id)
            )
        return zone

    @staticmethod
    async def _get_live_zone(session: AsyncSession, event_zone_id: int) -> EventZone:
        zone = await session.scalar(
            select(EventZone)
            .where(EventZone.id == event_zone_id)
            .where(EventZone.deleted_at == None)
        )
        if not zone:
            raise EntityNotFoundError(
                EVENT_ZONE_NOT_FOUND_MSG.format(event_zone_id=event_zone_id, event_id="?")
            )
        return zone

    @staticmethod
    async def _count_live_seats(session: AsyncSession, event_zone_id: int) -> int:
        total = await session.scalar(
            select(func.count())
            .select_from(EventSeat)
            .where(EventSeat.event_zone_id == event_zone_id)
            .where(EventSeat.deleted_at == None)
        )
        return total or 0

    @staticmethod
    async def _next_serial_number(session: AsyncSession, event_zone_id: int) -> int:
        """Next serial number in a zone. Counts soft-deleted rows so a retired
        Redis offset is never handed to a different seat."""
        current_max = await session.scalar(
            select(func.max(EventSeat.serial_number)).where(
                EventSeat.event_zone_id == event_zone_id
            )
        )
        return (current_max or 0) + 1

    @staticmethod
    async def _read_layout(session: AsyncSession, event: Event) -> EventLayoutInternal:
        rows = await session.execute(
            select(EventZone, func.count(EventSeat.id))
            .outerjoin(
                EventSeat,
                (EventSeat.event_zone_id == EventZone.id) & (EventSeat.deleted_at == None),
            )
            .where(EventZone.event_id == event.id)
            .where(EventZone.deleted_at == None)
            .group_by(EventZone.id)
            .order_by(EventZone.name)
        )
        return EventLayoutInternal(
            event=event,
            zones=[
                EventZoneWithSeatCountInternal(zone=zone, seat_count=seat_count)
                for zone, seat_count in rows.all()
            ],
        )
