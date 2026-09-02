from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from dependency_injector.wiring import inject, Provide
from app.containers import Container
from app.services.venue import VenueService
from app.schemas import Page, PaginationParams
from app.schemas.venue import (
    VenueCreate,
    VenueResponse,
    ZoneBase,
    ZoneResponse,
    SeatBatchCreateRequest,
    SeatCreate,
    SeatResponse,
)
from app.exceptions import EntityNotFoundError, EntityConflictError, ActiveEventsExistError

router = APIRouter(tags=["venues"])


@router.post("/venues", response_model=VenueResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_venue(
    venue_data: VenueCreate,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Creates a new venue."""
    return await venue_service.create_venue(venue_data)


@router.get("/venues", response_model=Page[VenueResponse], status_code=status.HTTP_200_OK)
@inject
async def list_venues(
    pagination: Annotated[PaginationParams, Query()],
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Lists active non-deleted venues, one page at a time."""
    return await venue_service.list_venues(pagination)


@router.get("/venues/{venue_id}", response_model=VenueResponse, status_code=status.HTTP_200_OK)
@inject
async def get_venue(
    venue_id: int,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Gets details of an active venue by its ID."""
    try:
        return await venue_service.get_venue(venue_id)
    except EntityNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.delete("/venues/{venue_id}", status_code=status.HTTP_200_OK)
@inject
async def delete_venue(
    venue_id: int,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """
    Soft-deletes a venue along with all its zones and seats.
    Rejects the operation if active events are linked to the venue.
    """
    try:
        await venue_service.delete_venue(venue_id)
        return {"success": True}
    except EntityNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ActiveEventsExistError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/venues/{venue_id}/zones", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_zone(
    venue_id: int,
    zone_data: ZoneBase,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Creates a new zone under a venue."""
    try:
        return await venue_service.create_zone(venue_id, zone_data)
    except EntityNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except EntityConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.get("/venues/{venue_id}/zones", response_model=Page[ZoneResponse], status_code=status.HTTP_200_OK)
@inject
async def list_zones_by_venue(
    venue_id: int,
    pagination: Annotated[PaginationParams, Query()],
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Lists active zones under a venue, one page at a time."""
    try:
        return await venue_service.list_zones_by_venue(venue_id, pagination)
    except EntityNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/zones/{zone_id}", response_model=ZoneResponse, status_code=status.HTTP_200_OK)
@inject
async def get_zone(
    zone_id: int,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Gets details of an active zone by its ID."""
    try:
        return await venue_service.get_and_check_zone(zone_id)
    except EntityNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/zones/{zone_id}/seats", response_model=SeatResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_seat(
    zone_id: int,
    seat_data: SeatCreate,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Creates a single seat under a zone."""
    try:
        return await venue_service.create_seat(zone_id, seat_data)
    except EntityNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except EntityConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/zones/{zone_id}/seats/batch", response_model=list[SeatResponse], status_code=status.HTTP_201_CREATED)
@inject
async def create_seats_batch(
    zone_id: int,
    batch_data: SeatBatchCreateRequest,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Batch creates multiple seats under a zone."""
    try:
        return await venue_service.batch_create_seats(zone_id, batch_data.seats)
    except EntityNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except EntityConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.get("/zones/{zone_id}/seats", response_model=Page[SeatResponse], status_code=status.HTTP_200_OK)
@inject
async def list_seats_by_zone(
    zone_id: int,
    pagination: Annotated[PaginationParams, Query()],
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Lists active seats under a zone, one page at a time."""
    try:
        return await venue_service.list_seats_by_zone(zone_id, pagination)
    except EntityNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/seats/{seat_id}", response_model=SeatResponse, status_code=status.HTTP_200_OK)
@inject
async def get_seat(
    seat_id: int,
    venue_service: VenueService = Depends(Provide[Container.venue_service]),
):
    """Gets details of an active seat by its ID."""
    try:
        return await venue_service.get_seat(seat_id)
    except EntityNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
