"""Event CRUD API.

Domain errors raised by the Service are translated to HTTP status codes by the
exception handlers registered in `app.main`, so handlers stay free of
try/except boilerplate.
"""
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from dependency_injector.wiring import inject, Provide

from app.containers import Container
from app.routers._auth import AnyManager
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
admin_router = APIRouter(prefix="/admin/events", tags=["events"])


# --------------------------------------------------------------------------- #
# Event
# --------------------------------------------------------------------------- #
@admin_router.post("", response_model=EventDetailResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_event(
    payload: EventCreate,
    _: AnyManager,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Creates an unpublished event and clones the venue's zone/seat layout."""
    return await event_service.create_event(payload)

@admin_router.get("", response_model=Page[EventResponse], status_code=status.HTTP_200_OK)
@inject
async def list_all_events(
    pagination: Annotated[PaginationParams, Query()],
    _: AnyManager,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Lists non-deleted events, one page at a time."""
    return await event_service.list_events(pagination)


@router.get("/selling", response_model=Page[EventResponse], status_code=status.HTTP_200_OK)
@inject
async def list_selling_events(
    pagination: Annotated[PaginationParams, Query()],
    background_tasks: BackgroundTasks,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Lists selling events, one page at a time."""
    return await event_service.list_selling_events(pagination, background_tasks)


@router.get("/{event_id}", response_model=EventDetailResponse, status_code=status.HTTP_200_OK)
@inject
async def get_event(
    event_id: int,
    background_tasks: BackgroundTasks,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Gets a published event with its zones. Drafts read as 404."""
    return await event_service.get_event(event_id, background_tasks)


@admin_router.get("/{event_id}", response_model=EventDetailResponse, status_code=status.HTTP_200_OK)
@inject
async def get_event_for_manager(
    event_id: int,
    _: AnyManager,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Gets an event with its zones, drafts included."""
    return await event_service.get_event_for_manager(event_id)


@admin_router.patch("/{event_id}/profile", response_model=EventResponse, status_code=status.HTTP_200_OK)
@inject
async def update_event_profile(
    event_id: int,
    payload: EventProfileUpdate,
    background_tasks: BackgroundTasks,
    _: AnyManager,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Updates the event's name and description."""
    return await event_service.update_event_profile(event_id, payload, background_tasks)


@admin_router.patch("/{event_id}/holding_time", response_model=EventResponse, status_code=status.HTTP_200_OK)
@inject
async def update_event_holding_time(
    event_id: int,
    payload: EventHoldingTimeUpdate,
    _: AnyManager,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Updates the event's holding time. Rejected once the event is published."""
    return await event_service.update_event_holding_time(event_id, payload)


@admin_router.post("/{event_id}/publish", response_model=EventResponse, status_code=status.HTTP_200_OK)
@inject
async def publish_event(
    event_id: int,
    background_tasks: BackgroundTasks,
    _: AnyManager,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Puts the event on sale. Rejected while any zone is still unpriced."""
    return await event_service.publish_event(event_id, background_tasks)


@admin_router.post("/{event_id}/unpublish", response_model=EventResponse, status_code=status.HTTP_200_OK)
@inject
async def unpublish_event(
    event_id: int,
    background_tasks: BackgroundTasks,
    _: AnyManager,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Takes the event off sale. Rejected once any order exists."""
    return await event_service.unpublish_event(event_id, background_tasks)


@admin_router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_event(
    event_id: int,
    background_tasks: BackgroundTasks,
    _: AnyManager,
    event_service: EventService = Depends(Provide[Container.event_service]),
):
    """Soft-deletes an event. Rejected when confirmed orders exist."""
    await event_service.delete_event(event_id, background_tasks)


# --------------------------------------------------------------------------- #
# EventZone / EventSeat - served by EventLayoutService
# --------------------------------------------------------------------------- #
@admin_router.post(
    "/{event_id}/event_zones",
    response_model=EventZoneResponse,
    status_code=status.HTTP_201_CREATED,
)
@inject
async def create_event_zone(
    event_id: int,
    payload: EventZoneCreate,
    _: AnyManager,
    layout_service: EventLayoutService = Depends(Provide[Container.event_layout_service]),
):
    """Creates a zone and its seats. Rejected once the event is published."""
    return await layout_service.create_event_zone(event_id, payload)


@admin_router.patch(
    "/{event_id}/event_zones/{event_zone_id}/profile",
    response_model=EventZoneResponse,
    status_code=status.HTTP_200_OK,
)
@inject
async def update_event_zone_profile(
    event_id: int,
    event_zone_id: int,
    payload: EventZoneProfileUpdate,
    background_tasks: BackgroundTasks,
    _: AnyManager,
    layout_service: EventLayoutService = Depends(Provide[Container.event_layout_service]),
):
    """Updates a zone's name and ticket price."""
    return await layout_service.update_event_zone_profile(
        event_id, event_zone_id, payload, background_tasks
    )


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
    """Lists the zone's seat layout and each seat's current status. Drafts read as 404."""
    return await layout_service.list_event_zone_seats(event_id, event_zone_id, pagination)


@admin_router.get(
    "/{event_id}/event_zones/{event_zone_id}/seats",
    response_model=Page[EventSeatResponse],
    status_code=status.HTTP_200_OK,
)
@inject
async def list_event_zone_seats_for_manager(
    event_id: int,
    event_zone_id: int,
    pagination: Annotated[PaginationParams, Query()],
    _: AnyManager,
    layout_service: EventLayoutService = Depends(Provide[Container.event_layout_service]),
):
    """Same as the public route, drafts included."""
    return await layout_service.list_event_zone_seats(
        event_id, event_zone_id, pagination, include_unpublished=True
    )


@admin_router.patch(
    "/{event_id}/event_zones/{event_zone_id}/seats",
    response_model=EventSeatLayoutSyncResponse,
    status_code=status.HTTP_200_OK,
)
@inject
async def replace_event_zone_seats(
    event_id: int,
    event_zone_id: int,
    payload: EventSeatLayoutUpdate,
    _: AnyManager,
    layout_service: EventLayoutService = Depends(Provide[Container.event_layout_service]),
):
    """Replaces the zone's seat layout wholesale. Rejected once published."""
    return await layout_service.replace_event_zone_seats(event_id, event_zone_id, payload)
