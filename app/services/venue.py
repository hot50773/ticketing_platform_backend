from app.models._models import Venue, Zone, Seat
from app.schemas import VenueCreate, ZoneCreate, SeatCreate
from app.repositories.venue import VenueRepository
from app.exceptions import EntityNotFoundError


class VenueService:
    """Service layer orchestrating business logic and validation for Venue, Zone, and Seat."""

    def __init__(self, venue_repository: VenueRepository):
        self._repository = venue_repository

    async def create_venue(self, venue_data: VenueCreate) -> Venue:
        """Creates a new venue after validating business rules."""
        return await self._repository.create_venue(venue_data)

    async def list_venues(self) -> list[Venue]:
        """Lists all active non-deleted venues."""
        return await self._repository.get_venues()

    async def get_venue(self, venue_id: int) -> Venue:
        """Retrieves an active venue by ID or raises EntityNotFoundError if missing."""
        venue = await self._repository.get_venue_by_id(venue_id)
        if not venue:
            raise EntityNotFoundError(f"Venue with id {venue_id} not found")
        return venue

    async def delete_venue(self, venue_id: int) -> None:
        """Performs cascading soft delete for a venue and its zones/seats."""
        await self._repository.delete_venue_cascade(venue_id)

    async def create_zone(self, venue_id: int, zone_data: ZoneCreate) -> Zone:
        """Creates a new zone under a venue."""
        return await self._repository.create_zone(venue_id, zone_data)

    async def list_zones_by_venue(self, venue_id: int) -> list[Zone]:
        """Lists all active zones belonging to a venue."""
        return await self._repository.get_zones_by_venue_id(venue_id)

    async def get_zone(self, zone_id: int) -> Zone:
        """Retrieves an active zone by ID or raises EntityNotFoundError if missing."""
        zone = await self._repository.get_zone_by_id(zone_id)
        if not zone:
            raise EntityNotFoundError(f"Zone with id {zone_id} not found")
        return zone

    async def create_seat(self, zone_id: int, seat_data: SeatCreate) -> Seat:
        """Creates a single seat under a zone."""
        return await self._repository.create_seat(zone_id, seat_data)

    async def batch_create_seats(self, zone_id: int, seats_data: list[SeatCreate]) -> list[Seat]:
        """Batch creates multiple seats under a zone."""
        return await self._repository.create_seats_batch(zone_id, seats_data)

    async def list_seats_by_zone(self, zone_id: int) -> list[Seat]:
        """Lists all active seats belonging to a zone."""
        return await self._repository.get_seats_by_zone_id(zone_id)

    async def get_seat(self, seat_id: int) -> Seat:
        """Retrieves an active seat by ID or raises EntityNotFoundError if missing."""
        seat = await self._repository.get_seat_by_id(seat_id)
        if not seat:
            raise EntityNotFoundError(f"Seat with id {seat_id} not found")
        return seat
