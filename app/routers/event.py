from fastapi import APIRouter, Depends, HTTPException
from dependency_injector.wiring import inject, Provide
from app.containers import Container
from app.services.event import EventService
from app.schemas import LockSeatRequest, UnlockSeatRequest, LockSeatsRequest, UnlockSeatsRequest

router = APIRouter(prefix="/events", tags=["events"])

@router.get("/{event_id}/seats")
@inject
async def list_event_seats(
    event_id: int,
    event_service: EventService = Depends(Provide[Container.event_service])
):
    """Lists all seats and their booking status for a specific event."""
    return await event_service.list_seats(event_id)

@router.post("/{event_id}/seats/lock")
@inject
async def lock_event_seats(
    event_id: int,
    req: LockSeatsRequest,
    event_service: EventService = Depends(Provide[Container.event_service])
):
    """Locks multiple seats for a specific event and user."""
    success = await event_service.lock_seats(event_id, req.seat_ids, req.user_id)
    if not success:
        raise HTTPException(status_code=409, detail="Failed to lock seats. Some seats may be already locked or reserved.")
    return {"success": True}

@router.post("/{event_id}/seats/unlock")
@inject
async def unlock_event_seats(
    event_id: int,
    req: UnlockSeatsRequest,
    event_service: EventService = Depends(Provide[Container.event_service])
):
    """Unlocks multiple seats for a specific event and user, releasing them back to public."""
    success = await event_service.unlock_seats(event_id, req.seat_ids, req.user_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to unlock seats. Ensure they belong to your active order.")
    return {"success": True}

@router.post("/{event_id}/seats/{seat_id}/lock")
@inject
async def lock_event_seat(
    event_id: int,
    seat_id: int,
    req: LockSeatRequest,
    event_service: EventService = Depends(Provide[Container.event_service])
):
    """Locks a seat for a specific event and user."""
    success = await event_service.lock_seat(event_id, seat_id, req.user_id)
    if not success:
        raise HTTPException(status_code=409, detail="Seat is already locked or reserved.")
    return {"success": True}


@router.post("/{event_id}/seats/{seat_id}/unlock")
@inject
async def unlock_event_seat(
    event_id: int,
    seat_id: int,
    req: UnlockSeatRequest,
    event_service: EventService = Depends(Provide[Container.event_service])
):
    """Unlocks a seat for a specific event and user, releasing it back to public."""
    success = await event_service.unlock_seat(event_id, seat_id, req.user_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to unlock seat.")
    return {"success": True}

