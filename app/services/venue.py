from app.models import Venue, Zone, Seat
from app.schemas import Page, PaginationParams
from app.schemas.venue import VenueCreate, ZoneBase, SeatCreate
from app.repositories.venue import VenueRepository
from app.exceptions import EntityNotFoundError, VENUE_NOT_FOUND_MSG, SEAT_NOT_FOUND_MSG


class VenueService:
    """Service layer orchestrating business logic and validation for Venue, Zone, and Seat."""
    def __init__(self, venue_repository: VenueRepository):
        self._repository = venue_repository

    async def create_venue(self, venue_data: VenueCreate) -> Venue:
        return await self._repository.create_venue(venue_data)

    async def list_venues(self, pagination: PaginationParams) -> Page[Venue]:
        return await self._repository.get_venues(pagination)

    async def get_venue(self, venue_id: int) -> Venue:
        venue = await self._repository.get_and_check_venue_by_id(venue_id)
        if not venue:
            raise EntityNotFoundError(VENUE_NOT_FOUND_MSG.format(venue_id=venue_id))
        return venue

    async def delete_venue(self, venue_id: int) -> None:
        await self._repository.delete_venue_cascade(venue_id)

    async def create_zone(self, venue_id: int, zone_data: ZoneBase) -> Zone:
        return await self._repository.create_zone(venue_id, zone_data)

    async def list_zones_by_venue(
        self, venue_id: int, pagination: PaginationParams
    ) -> Page[Zone]:
        return await self._repository.get_zones_by_venue_id(venue_id, pagination)

    async def get_and_check_zone(self, zone_id: int) -> Zone:
        return await self._repository.get_and_check_zone(zone_id)

    async def create_seat(self, zone_id: int, seat_data: SeatCreate) -> Seat:
        await self.get_and_check_zone(zone_id)
        return await self._repository.create_seat(zone_id, seat_data)

    async def batch_create_seats(
        self, zone_id: int, seats_data: list[SeatCreate]
    ) -> list[Seat]:
        await self.get_and_check_zone(zone_id)
        return await self._repository.create_seats_batch(zone_id, seats_data)

    async def list_seats_by_zone(
        self, zone_id: int, pagination: PaginationParams
    ) -> Page[Seat]:
        return await self._repository.get_seats_by_zone_id(zone_id, pagination)

    async def get_seat(self, seat_id: int) -> Seat:
        return await self._repository.get_and_check_seat(seat_id)
