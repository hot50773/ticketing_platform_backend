from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.exceptions import (
    EntityNotFoundError,
    EntityConflictError,
    ActiveEventsExistError,
    VENUE_NOT_FOUND_MSG,
    ZONE_NOT_FOUND_MSG,
    SEAT_NOT_FOUND_MSG,
)
from app.models import Venue, Zone, Seat, Event
from app.schemas import VenueCreate, ZoneCreate, SeatCreate
from app.libs.clock.functional import now_utc


class VenueRepository:
    """Repository class managing database transactions and persistence for Venue, Zone, and Seat."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def create_venue(self, venue_data: VenueCreate) -> Venue:
        """Persists a new Venue record in the database."""
        async with self.session_factory() as session:
            venue = self._create_venue(session, venue_data)
            await session.commit()
            await session.refresh(venue)
            return venue

    @staticmethod
    def _create_venue(session: AsyncSession, venue_data: VenueCreate) -> Venue:
        venue = Venue(
            name=venue_data.name,
            description=venue_data.description,
            address=venue_data.address,
        )
        session.add(venue)
        return venue

    async def get_venues(self) -> list[Venue]:
        """Retrieves all non-deleted venues ordered by ID."""
        async with self.session_factory() as session:
            stmt = select(Venue).where(Venue.deleted_at == None).order_by(Venue.id)
            res = await session.execute(stmt)
            return res.scalars().all()

    async def get_and_check_venue_by_id(self, venue_id: int) -> Venue:
        async with self.session_factory() as session:
            return await self._get_and_check_venue(session, venue_id)

    async def delete_venue_cascade(self, venue_id: int) -> None:
        """
        Soft-deletes a venue, cascading to all associated zones and seats in a single transaction.
        Aborts if any active non-deleted events are linked to the venue.
        """
        async with self.session_factory() as session:
            now = now_utc()
            await self._delete_venue(session, venue_id, now)

            zone_ids = await self._delete_zones_by_venue_id(session, venue_id, now)
            if zone_ids:
                await self._delete_seats_by_zone_ids(session, zone_ids, now)
            await session.commit()

    async def _delete_venue(self, session: AsyncSession, venue_id: int, now: datetime) -> None:
        venue = await self._get_and_check_venue(session, venue_id)
        active_events = await self._check_active_events_by_venue_id(session, venue_id)
        if active_events:
            raise ActiveEventsExistError(
                f"Cannot delete venue {venue_id} because active non-deleted events are associated with it"
            )
        venue.deleted_at = now
        session.add(venue)

    @staticmethod
    async def _check_active_events_by_venue_id(session: AsyncSession, venue_id: int) -> list[Event]:
        stmt_events = (
            select(Event)
            .where(Event.venue_id == venue_id)
            .where(Event.deleted_at == None)
        )
        res_events = await session.execute(stmt_events)
        active_events = res_events.scalars().all()
        return active_events

    @staticmethod
    async def _delete_seats_by_zone_ids(session: AsyncSession, zone_ids: list[int], now: datetime):
        stmt_seats = (
            select(Seat)
            .where(Seat.zone_id.in_(zone_ids))
            .where(Seat.deleted_at == None)
        )
        res_seats = await session.execute(stmt_seats)
        seats = list(res_seats.scalars().all())
        for s in seats:
            s.deleted_at = now
            session.add(s)

    @staticmethod
    async def _delete_zones_by_venue_id(session: AsyncSession, venue_id: int, now: datetime) -> list[Zone]:
        stmt = (
            select(Zone)
            .where(Zone.venue_id == venue_id)
            .where(Zone.deleted_at == None)
        )
        res = await session.execute(stmt)
        zones = res.scalars().all()

        for z in zones:
            z.deleted_at = now
        session.add_all(zones)

        zone_ids = [z.id for z in zones]
        return zone_ids

    async def create_zone(self, venue_id: int, zone_data: ZoneCreate) -> Zone:
        async with self.session_factory() as session:
            # Ensure the parent venue exists and is not soft-deleted
            venue = await self._get_and_check_venue(session, venue_id)
            if not venue:
                raise EntityNotFoundError(VENUE_NOT_FOUND_MSG.format(venue_id=venue_id))

            zone = Zone(code=zone_data.code, venue_id=venue_id)
            session.add(zone)
            try:
                await session.commit()
                await session.refresh(zone)
                return zone
            except IntegrityError as exc:
                await session.rollback()
                raise EntityConflictError(
                    f"Zone code '{zone_data.code}' already exists for venue {venue_id}"
                ) from exc

    async def get_zones_by_venue_id(self, venue_id: int) -> list[Zone]:
        async with self.session_factory() as session:
            stmt = (
                select(Zone)
                .join(Venue, Zone.venue_id == Venue.id)
                .where(Zone.venue_id == venue_id)
                .where(Zone.deleted_at == None)
                .order_by(Zone.id)
            )
            res = await session.execute(stmt)
            return res.scalars().all()

    @staticmethod
    async def _get_and_check_venue(
        session: AsyncSession, venue_id: int
    ) -> Venue:
        stmt = select(Venue).where(Venue.id == venue_id).where(Venue.deleted_at == None)
        result = await session.execute(stmt)
        venue = result.scalar_one_or_none()
        if not venue:
            raise EntityNotFoundError(VENUE_NOT_FOUND_MSG.format(venue_id=venue_id))
        return venue

    async def get_and_check_zone(self, zone_id: int) -> Zone:
        async with self.session_factory() as session:
            return await self._get_and_check_zone(session, zone_id)

    @staticmethod
    async def _get_and_check_zone(session: AsyncSession, zone_id: int) -> Zone:
        stmt = (
            select(Zone)
            .join(Venue, Venue.id == Zone.venue_id)
            .where(Zone.id == zone_id)
            .where(Zone.deleted_at == None)
        )
        res = await session.execute(stmt)
        zone = res.scalar_one_or_none()
        if not zone:
            raise EntityNotFoundError(ZONE_NOT_FOUND_MSG.format(zone_id=zone_id))
        return zone

    async def create_seat(self, zone_id: int, seat_data: SeatCreate) -> Seat:
        async with self.session_factory() as session:
            seat = Seat(
                serial_number=seat_data.serial_number,
                row=seat_data.row,
                column=seat_data.column,
                zone_id=zone_id,
            )
            session.add(seat)
            try:
                await session.commit()
                await session.refresh(seat)
                return seat
            except IntegrityError as exc:
                await session.rollback()
                raise EntityConflictError(
                    f"Seat serial number {seat_data.serial_number} already exists in zone {zone_id}"
                ) from exc

    async def create_seats_batch(
        self, zone_id: int, seats_data: list[SeatCreate]
    ) -> None:
        async with self.session_factory() as session:
            # Validate for internal duplicate serial numbers within the batch request
            serial_numbers = [s.serial_number for s in seats_data]
            if len(serial_numbers) != len(set(serial_numbers)):
                raise EntityConflictError(
                    "Duplicate seat serial numbers provided in batch payload"
                )
            seats = [
                Seat(
                    serial_number=s.serial_number,
                    row=s.row,
                    column=s.column,
                    zone_id=zone_id,
                )
                for s in seats_data
            ]
            session.add_all(seats)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise EntityConflictError(
                    f"One or more seat serial numbers already exist in zone {zone_id}"
                ) from exc

    async def get_seats_by_zone_id(self, zone_id: int) -> list[Seat]:
        async with self.session_factory() as session:
            zone = self._get_and_check_zone(session, zone_id)

            stmt = (
                select(Seat)
                .where(Seat.zone_id == zone_id)
                .where(Seat.deleted_at == None)
                .order_by(Seat.serial_number)
            )
            res = await session.execute(stmt)
            return res.scalars().all()

    async def get_and_check_seat(self, seat_id: int) -> Seat:
        async with self.session_factory() as session:
            stmt = select(Seat).where(Seat.id == seat_id).where(Seat.deleted_at == None)
            res = await session.execute(stmt)
            seat = res.scalar_one_or_none()
            if not seat:
                raise EntityNotFoundError(SEAT_NOT_FOUND_MSG.format(seat_id=seat_id))
            return seat
