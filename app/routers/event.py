"""Event CRUD API.

Domain errors raised by the Service are translated to HTTP status codes by the
exception handlers registered in `app.main`, so handlers stay free of
try/except boilerplate.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from dependency_injector.wiring import inject, Provide

from app.containers import Container
from app.services.event import EventService
from app.services.event_layout import EventLayoutService
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    EventCreate,
    EventDetailResponse,
    EventHoldingTimeUpdate,
    EventProfileUpdate,
    EventResponse,
    EventSeatLayoutSyncResponse,
    EventSeatLayoutUpdate,
    EventSeatResponse,
    EventZoneCreate,
    EventZoneProfileUpdate,
    EventZoneResponse,
)

router = APIRouter(prefix="/events", tags=["events"])


# --------------------------------------------------------------------------- #
# Event
# --------------------------------------------------------------------------- #
@router.post("", response_model=EventDetailResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_event(
    payload: EventCreate,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Creates an unpublished event and clones the venue's zone/seat layout."""
    return await event_service.create_event(payload)

# TODO: 加入後台設計後，要改成只有 admin 可讀取
@router.get("/all", response_model=Page[EventResponse], status_code=status.HTTP_200_OK)
@inject
async def list_all_events(
    pagination: Annotated[PaginationParams, Query()],
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Lists non-deleted events, one page at a time."""
    return await event_service.list_events(pagination)


@router.get("/selling", response_model=Page[EventResponse], status_code=status.HTTP_200_OK)
@inject
async def list_selling_events(
    pagination: Annotated[PaginationParams, Query()],
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Lists selling events, one page at a time."""
    return await event_service.list_selling_events(pagination)


@router.get("/{event_id}", response_model=EventDetailResponse, status_code=status.HTTP_200_OK)
@inject
async def get_event(
    event_id: int,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Gets an event with its zones and seats."""
    return await event_service.get_event(event_id)


@router.patch("/{event_id}/profile", response_model=EventResponse, status_code=status.HTTP_200_OK)
@inject
async def update_event_profile(
    event_id: int,
    payload: EventProfileUpdate,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Updates the event's name and description."""
    return await event_service.update_event_profile(event_id, payload)


@router.patch("/{event_id}/holding_time", response_model=EventResponse, status_code=status.HTTP_200_OK)
@inject
async def update_event_holding_time(
    event_id: int,
    payload: EventHoldingTimeUpdate,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Updates the event's holding time. Rejected once the event is published."""
    return await event_service.update_event_holding_time(event_id, payload)


@router.post("/{event_id}/publish", response_model=EventResponse, status_code=status.HTTP_200_OK)
@inject
async def publish_event(
    event_id: int,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Puts the event on sale. Rejected while any zone is still unpriced."""
    return await event_service.publish_event(event_id)


@router.post("/{event_id}/unpublish", response_model=EventResponse, status_code=status.HTTP_200_OK)
@inject
async def unpublish_event(
    event_id: int,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Takes the event off sale. Rejected once any order exists."""
    return await event_service.unpublish_event(event_id)


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_event(
    event_id: int,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Soft-deletes an event. Rejected when confirmed orders exist."""
    await event_service.delete_event(event_id)


# --------------------------------------------------------------------------- #
# EventZone / EventSeat - served by EventLayoutService
# --------------------------------------------------------------------------- #
@router.post(
    "/{event_id}/event_zones",
    response_model=EventZoneResponse,
    status_code=status.HTTP_201_CREATED,
)
@inject
async def create_event_zone(
    event_id: int,
    payload: EventZoneCreate,
    layout_service: EventLayoutService = Depends(Provide[Container.event_layout_service]),
):
    """Creates a zone and its seats. Rejected once the event is published."""
    return await layout_service.create_event_zone(event_id, payload)


@router.patch(
    "/{event_id}/event_zones/{event_zone_id}/profile",
    response_model=EventZoneResponse,
    status_code=status.HTTP_200_OK,
)
@inject
async def update_event_zone_profile(
    event_id: int,
    event_zone_id: int,
    payload: EventZoneProfileUpdate,
    layout_service: EventLayoutService = Depends(Provide[Container.event_layout_service]),
):
    """Updates a zone's name and ticket price."""
    return await layout_service.update_event_zone_profile(event_id, event_zone_id, payload)


@router.get(
    "/{event_id}/event_zones/{event_zone_id}/seats",
    response_model=Page[EventSeatResponse],
    status_code=status.HTTP_200_OK,
)
@inject
async def list_event_zone_seats(
    event_id: int,
    event_zone_id: int,
    pagination: Annotated[PaginationParams, Query()],
    layout_service: EventLayoutService = Depends(Provide[Container.event_layout_service]),
):
    """Lists the zone's seat layout and each seat's current status."""
    return await layout_service.list_event_zone_seats(event_id, event_zone_id, pagination)


@router.patch(
    "/{event_id}/event_zones/{event_zone_id}/seats",
    response_model=EventSeatLayoutSyncResponse,
    status_code=status.HTTP_200_OK,
)
@inject
async def replace_event_zone_seats(
    event_id: int,
    event_zone_id: int,
    payload: EventSeatLayoutUpdate,
    layout_service: EventLayoutService = Depends(Provide[Container.event_layout_service]),
):
    """Replaces the zone's seat layout wholesale. Rejected once published."""
    return await layout_service.replace_event_zone_seats(event_id, event_zone_id, payload)
