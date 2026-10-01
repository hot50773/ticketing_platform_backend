"""Gateway to the Event aggregate.

Instead of exposing one method per query, this hands out an EventAggregate that
already owns a transaction: a use case enters it once, checks its rules and
writes, and everything commits together. See app/aggregates/event.py for the
concurrency contract behind load_for_update().

Only genuinely collection-level queries stay here - ones that do not belong to
any single aggregate instance.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import cast, func, insert, literal
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.aggregates.event import EventAggregate
from app.enums import SeatStatus
from app.exceptions import (
    EVENT_NOT_FOUND_MSG,
    VENUE_NOT_FOUND_MSG,
    EntityNotFoundError,
)
from app.models import Event, EventSeat, EventZone, Seat, Venue, Zone
from app.repositories._pagination import paginate
from app.schemas import Page, PaginationParams
from app.schemas.event import EventCreate


class EventRepository:
    """Opens transactions and hands the use case an EventAggregate inside one."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    # ----------------------------------------------------------------------- #
    # Aggregate access
    # ----------------------------------------------------------------------- #
    @asynccontextmanager
    async def load(self, event_id: int) -> AsyncIterator[EventAggregate]:
        """Read-only access. Takes no row lock.

        The rollback is belt-and-braces on a read path - there should be
        nothing pending to undo. It is here so all three entry points unwind
        the same way, and so a write accidentally issued through `load()` is
        discarded loudly rather than left to the pool.
        """
        async with self.session_factory() as session:
            try:
                yield EventAggregate(session, await self._get_and_check_event(session, event_id))
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def load_for_update(self, event_id: int) -> AsyncIterator[EventAggregate]:
        """Write access. Locks the event row with `FOR UPDATE`, commits on a
        clean exit and rolls back explicitly on any exception.

        The rollback is not redundant. Closing the session would return the
        connection to a pool whose reset_on_return is "rollback", so the
        transaction would unwind anyway - but that guarantee lives three
        layers away in engine defaults. Stated here, it survives someone
        swapping the pool config, and it releases the FOR UPDATE lock at a
        point this code controls rather than whenever the pool gets around
        to recycling the connection.

        The lock is what actually closes the check-then-write race: one
        transaction alone would still let a concurrent order slip in between
        counting and updating under READ COMMITTED.
        """
        async with self.session_factory() as session:
            try:
                event = await self._get_and_check_event(session, event_id, for_update=True)
                yield EventAggregate(session, event)
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def create(self, payload: EventCreate) -> AsyncIterator[EventAggregate]:
        """Creates an unpublished event, clones the venue's layout into it, and
        hands back the aggregate still inside the same transaction.

        EventSeat.serial_number is copied straight from Seat.serial_number, so a
        zone's numbering can have holes where template seats were deleted.
        Cloning uses `INSERT ... SELECT` rather than ORM row-by-row inserts: a
        10k-seat venue would otherwise round-trip 10k objects through Python.

        Raises EntityNotFoundError when the venue does not exist or is deleted.
        """
        async with self.session_factory() as session:
            try:
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
                await session.flush()
                await session.refresh(event)

                yield EventAggregate(session, event)
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    # ----------------------------------------------------------------------- #
    # Collection queries
    # ----------------------------------------------------------------------- #
    async def get_events(self, pagination: PaginationParams) -> Page[Event]:
        """One page of non-deleted events ordered by id, plus the total count."""
        async with self.session_factory() as session:
            stmt = select(Event).where(Event.deleted_at == None).order_by(Event.id)
            return await paginate(session, stmt, pagination, Event)

    # ----------------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------------- #
    @staticmethod
    async def _get_and_check_event(
        session: AsyncSession, event_id: int, for_update: bool = False
    ) -> Event:
        stmt = select(Event).where(Event.id == event_id).where(Event.deleted_at == None)
        if for_update:
            stmt = stmt.with_for_update()
        event = await session.scalar(stmt)
        if not event:
            raise EntityNotFoundError(EVENT_NOT_FOUND_MSG.format(event_id=event_id))
        return event

    @staticmethod
    async def _check_venue(session: AsyncSession, venue_id: int) -> None:
        venue = await session.scalar(
            select(Venue).where(Venue.id == venue_id).where(Venue.deleted_at == None)
        )
        if not venue:
            raise EntityNotFoundError(VENUE_NOT_FOUND_MSG.format(venue_id=venue_id))

    @staticmethod
    async def _clone_zones(session: AsyncSession, event_id: int, venue_id: int) -> None:
        """Copies the venue's existing zones. price stays NULL (not configured yet)."""
        # created_at / updated_at are spelled out rather than left to the
        # column defaults: those are `default_factory=func.now`, a *Python*
        # side default, so SQLAlchemy hands asyncpg the func.now() construct
        # as a bind parameter and asyncpg rejects it ("expected a datetime
        # instance, got 'now'"). Naming them here renders now() as SQL.
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
        """Copies the existing template seats of every cloned zone in one statement,
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
