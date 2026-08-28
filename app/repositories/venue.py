from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from app.models._models import Venue, Zone, Seat, Event
from app.schemas import VenueCreate, ZoneCreate, SeatCreate
from app.exceptions import EntityNotFoundError, EntityConflictError, ActiveEventsExistError


class VenueRepository:
    """Repository class managing database transactions and persistence for Venue, Zone, and Seat."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def create_venue(self, venue_data: VenueCreate) -> Venue:
        """Persists a new Venue record in the database."""
        async with self.session_factory() as session:
            venue = Venue(
                name=venue_data.name,
                description=venue_data.description,
                address=venue_data.address,
            )
            session.add(venue)
            await session.commit()
            await session.refresh(venue)
            return venue

    async def get_venues(self) -> list[Venue]:
        """Retrieves all non-deleted venues ordered by ID."""
        async with self.session_factory() as session:
            stmt = select(Venue).where(Venue.deleted_at == None).order_by(Venue.id)
            res = await session.execute(stmt)
            return list(res.scalars().all())

    async def get_venue_by_id(self, venue_id: int) -> Venue | None:
        """Retrieves a single non-deleted venue by its primary key ID."""
        async with self.session_factory() as session:
            stmt = select(Venue).where(Venue.id == venue_id).where(Venue.deleted_at == None)
            res = await session.execute(stmt)
            return res.scalar_one_or_none()

    async def delete_venue_cascade(self, venue_id: int) -> bool:
        """
        Soft-deletes a venue, cascading to all associated zones and seats in a single transaction.
        Aborts if any active non-deleted events are linked to the venue.
        """
        async with self.session_factory() as session:
            # Check if venue exists and is not already deleted
            stmt_venue = select(Venue).where(Venue.id == venue_id).where(Venue.deleted_at == None).with_for_update()
            res_venue = await session.execute(stmt_venue)
            venue = res_venue.scalar_one_or_none()
            if not venue:
                raise EntityNotFoundError(f"Venue with id {venue_id} not found")

            # Check for active non-deleted events linked to this venue to prevent dangling active events
            stmt_events = (
                select(Event)
                .where(Event.venue_id == venue_id)
                .where(Event.deleted_at == None)
            )
            res_events = await session.execute(stmt_events)
            active_events = res_events.scalars().all()
            if active_events:
                raise ActiveEventsExistError(
                    f"Cannot delete venue {venue_id} because active non-deleted events are associated with it"
                )

            now = datetime.now(timezone.utc).replace(tzinfo=None)

            # Soft-delete the venue
            venue.deleted_at = now
            session.add(venue)

            # Soft-delete all zones under this venue
            stmt_zones = (
                select(Zone)
                .where(Zone.venue_id == venue_id)
                .where(Zone.deleted_at == None)
            )
            res_zones = await session.execute(stmt_zones)
            zones = list(res_zones.scalars().all())
            zone_ids = [z.id for z in zones]

            for z in zones:
                z.deleted_at = now
                session.add(z)

            # Soft-delete all seats under all zones of this venue
            if zone_ids:
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

            await session.commit()
            return True

    async def create_zone(self, venue_id: int, zone_data: ZoneCreate) -> Zone:
        """Creates a new Zone under a specific Venue while enforcing unique code constraints."""
        async with self.session_factory() as session:
            # Ensure the parent venue exists and is not soft-deleted
            stmt_venue = select(Venue).where(Venue.id == venue_id).where(Venue.deleted_at == None)
            res_venue = await session.execute(stmt_venue)
            venue = res_venue.scalar_one_or_none()
            if not venue:
                raise EntityNotFoundError(f"Venue with id {venue_id} not found")

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
        """Retrieves all non-deleted zones under a specific venue."""
        async with self.session_factory() as session:
            stmt_venue = select(Venue).where(Venue.id == venue_id).where(Venue.deleted_at == None)
            res_venue = await session.execute(stmt_venue)
            if not res_venue.scalar_one_or_none():
                raise EntityNotFoundError(f"Venue with id {venue_id} not found")

            stmt = (
                select(Zone)
                .where(Zone.venue_id == venue_id)
                .where(Zone.deleted_at == None)
                .order_by(Zone.id)
            )
            res = await session.execute(stmt)
            return list(res.scalars().all())

    async def get_zone_by_id(self, zone_id: int) -> Zone | None:
        """Retrieves a single non-deleted zone by its primary key ID."""
        async with self.session_factory() as session:
            stmt = select(Zone).where(Zone.id == zone_id).where(Zone.deleted_at == None)
            res = await session.execute(stmt)
            return res.scalar_one_or_none()

    async def create_seat(self, zone_id: int, seat_data: SeatCreate) -> Seat:
        """Creates a single Seat under a Zone while enforcing serial_number uniqueness."""
        async with self.session_factory() as session:
            stmt_zone = select(Zone).where(Zone.id == zone_id).where(Zone.deleted_at == None)
            res_zone = await session.execute(stmt_zone)
            if not res_zone.scalar_one_or_none():
                raise EntityNotFoundError(f"Zone with id {zone_id} not found")

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

    async def create_seats_batch(self, zone_id: int, seats_data: list[SeatCreate]) -> list[Seat]:
        """Creates multiple Seats in bulk under a Zone within a single transaction."""
        if not seats_data:
            return []

        async with self.session_factory() as session:
            stmt_zone = select(Zone).where(Zone.id == zone_id).where(Zone.deleted_at == None)
            res_zone = await session.execute(stmt_zone)
            if not res_zone.scalar_one_or_none():
                raise EntityNotFoundError(f"Zone with id {zone_id} not found")

            # Validate for internal duplicate serial numbers within the batch request
            serial_numbers = [s.serial_number for s in seats_data]
            if len(serial_numbers) != len(set(serial_numbers)):
                raise EntityConflictError("Duplicate seat serial numbers provided in batch payload")

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
                for seat in seats:
                    await session.refresh(seat)
                return seats
            except IntegrityError as exc:
                await session.rollback()
                raise EntityConflictError(
                    f"One or more seat serial numbers already exist in zone {zone_id}"
                ) from exc

    async def get_seats_by_zone_id(self, zone_id: int) -> list[Seat]:
        """Retrieves all non-deleted seats under a specific zone ordered by serial number."""
        async with self.session_factory() as session:
            stmt_zone = select(Zone).where(Zone.id == zone_id).where(Zone.deleted_at == None)
            res_zone = await session.execute(stmt_zone)
            if not res_zone.scalar_one_or_none():
                raise EntityNotFoundError(f"Zone with id {zone_id} not found")

            stmt = (
                select(Seat)
                .where(Seat.zone_id == zone_id)
                .where(Seat.deleted_at == None)
                .order_by(Seat.serial_number)
            )
            res = await session.execute(stmt)
            return list(res.scalars().all())

    async def get_seat_by_id(self, seat_id: int) -> Seat | None:
        """Retrieves a single non-deleted seat by its primary key ID."""
        async with self.session_factory() as session:
            stmt = select(Seat).where(Seat.id == seat_id).where(Seat.deleted_at == None)
            res = await session.execute(stmt)
            return res.scalar_one_or_none()
