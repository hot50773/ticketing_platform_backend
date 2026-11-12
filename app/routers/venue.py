from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from dependency_injector.wiring import inject, Provide
from app.containers import Container
from app.routers._auth import get_current_manager
from app.services.venue import VenueService
from app.schemas import Page, PaginationParams
from app.schemas.venue import (
    VenueCreate,
    VenueResponse,
    VenueSummaryResponse,
    ZoneBase,
    ZoneResponse,
    SeatBatchCreateRequest,
    SeatCreate,
    SeatResponse,
)

customer_router = APIRouter(tags=["venues"])
manager_router = APIRouter(prefix="/admin", tags=["venues"], dependencies=[Depends(get_current_manager)])


@manager_router.post("/venues", response_model=VenueResponse, status_code=status.HTTP_201_CREATED)
@inject
async def manager_create_venue(
    venue_data: VenueCreate,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    return await venue_service.create_venue(venue_data)


@customer_router.get("/venues", response_model=Page[VenueSummaryResponse], status_code=status.HTTP_200_OK)
@inject
async def customer_list_venues(
    pagination: Annotated[PaginationParams, Query()],
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Lists active non-deleted venues, one page at a time."""
    return await venue_service.list_venues(pagination)


@customer_router.get("/venues/{venue_id}", response_model=VenueSummaryResponse, status_code=status.HTTP_200_OK)
@inject
async def customer_get_venue(
    venue_id: int,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Gets details of an active venue by its ID."""
    return await venue_service.get_venue(venue_id)


@manager_router.delete("/venues/{venue_id}", status_code=status.HTTP_200_OK)
@inject
async def manager_delete_venue(
    venue_id: int,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """
    Soft-deletes a venue along with all its zones and seats.
    Rejects the operation if active events are linked to the venue.
    """
    await venue_service.delete_venue(venue_id)
    return {"success": True}


@manager_router.post("/venues/{venue_id}/zones", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
@inject
async def manager_create_zone(
    venue_id: int,
    zone_data: ZoneBase,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Creates a new zone under a venue."""
    return await venue_service.create_zone(venue_id, zone_data)




@manager_router.post("/zones/{zone_id}/seats", response_model=SeatResponse, status_code=status.HTTP_201_CREATED)
@inject
async def manager_create_seat(
    zone_id: int,
    seat_data: SeatCreate,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Creates a single seat under a zone."""
    return await venue_service.create_seat(zone_id, seat_data)


@manager_router.post("/zones/{zone_id}/seats/batch", response_model=list[SeatResponse], status_code=status.HTTP_201_CREATED)
@inject
async def manager_create_seats_batch(
    zone_id: int,
    batch_data: SeatBatchCreateRequest,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Batch creates multiple seats under a zone."""
    return await venue_service.batch_create_seats(zone_id, batch_data.seats)


# --------------------------------------------------------------------------- #
# Back-office reads of the venue template. The storefront sees an event's own
# zones and seats instead, cloned from this template at event creation.
# --------------------------------------------------------------------------- #
@manager_router.get("/venues", response_model=Page[VenueResponse], status_code=status.HTTP_200_OK)
@inject
async def manager_list_venues(
    pagination: Annotated[PaginationParams, Query()],
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    return await venue_service.list_venues(pagination)


@manager_router.get("/venues/{venue_id}", response_model=VenueResponse, status_code=status.HTTP_200_OK)
@inject
async def manager_get_venue(
    venue_id: int,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    return await venue_service.get_venue(venue_id)


@manager_router.get("/venues/{venue_id}/zones", response_model=Page[ZoneResponse], status_code=status.HTTP_200_OK)
@inject
async def manager_list_zones_by_venue(
    venue_id: int,
    pagination: Annotated[PaginationParams, Query()],
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    return await venue_service.list_zones_by_venue(venue_id, pagination)


@manager_router.get("/zones/{zone_id}", response_model=ZoneResponse, status_code=status.HTTP_200_OK)
@inject
async def manager_get_zone(
    zone_id: int,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    return await venue_service.get_and_check_zone(zone_id)


@manager_router.get("/zones/{zone_id}/seats", response_model=Page[SeatResponse], status_code=status.HTTP_200_OK)
@inject
async def manager_list_seats_by_zone(
    zone_id: int,
    pagination: Annotated[PaginationParams, Query()],
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    return await venue_service.list_seats_by_zone(zone_id, pagination)


@manager_router.get("/seats/{seat_id}", response_model=SeatResponse, status_code=status.HTTP_200_OK)
@inject
async def manager_get_seat(
    seat_id: int,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    return await venue_service.get_seat(seat_id)
