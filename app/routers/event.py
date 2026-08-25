from fastapi import APIRouter, Depends, HTTPException
from dependency_injector.wiring import inject, Provide
from app.containers import Container
from app.services.event import EventService
from app.schemas import LockSeatRequest, UnlockSeatRequest

router = APIRouter(prefix="/events", tags=["events"])

@router.get("/{event_id}/seats")
@inject
async def list_event_seats(
    event_id: int,
    event_service: EventService = Depends(Provide[Container.event_service])
):
    """Lists all seats and their booking status for a specific event."""
    raise NotImplementedError()

@router.post("/{event_id}/seats/{seat_id}/lock")
@inject
async def lock_event_seat(
    event_id: int,
    seat_id: int,
    req: LockSeatRequest,
    event_service: EventService = Depends(Provide[Container.event_service])
):
    """Locks a seat for a specific event and user."""
    raise NotImplementedError()

@router.post("/{event_id}/seats/{seat_id}/unlock")
@inject
async def unlock_event_seat(
    event_id: int,
    seat_id: int,
    req: UnlockSeatRequest,
    event_service: EventService = Depends(Provide[Container.event_service])
):
    """Unlocks a seat for a specific event and user, releasing it back to public."""
    raise NotImplementedError()
