from fastapi import APIRouter, Depends, HTTPException
from dependency_injector.wiring import inject, Provide
from app.containers import Container
from app.services.seat import SeatService
from app.schemas import LockSeatRequest, UnlockSeatRequest

router = APIRouter(prefix="/events", tags=["events"])

@router.get("/{event_id}/seats")
@inject
async def list_event_seats(
    event_id: int,
    seat_service: SeatService = Depends(Provide[Container.seat_service])
):
    """Lists all seats and their booking status for a specific event."""
    try:
        return await seat_service.list_seats(event_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{event_id}/seats/{seat_id}/lock")
@inject
async def lock_event_seat(
    event_id: int,
    seat_id: int,
    req: LockSeatRequest,
    seat_service: SeatService = Depends(Provide[Container.seat_service])
):
    """Locks a seat for a specific event and user for 15 minutes."""
    try:
        success = await seat_service.lock_seat(event_id, seat_id, req.user_id)
        if not success:
            raise HTTPException(
                status_code=409,
                detail="Seat is already locked or sold by another user."
            )
        return {"message": "Seat successfully locked.", "expired_in_minutes": 15}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{event_id}/seats/{seat_id}/unlock")
@inject
async def unlock_event_seat(
    event_id: int,
    seat_id: int,
    req: UnlockSeatRequest,
    seat_service: SeatService = Depends(Provide[Container.seat_service])
):
    """Unlocks a seat for a specific event and user, releasing it back to public."""
    try:
        success = await seat_service.unlock_seat(event_id, seat_id, req.user_id)
        if not success:
            raise HTTPException(
                status_code=400,
                detail="Failed to release lock. The seat might not be locked by this user, or lock already expired."
            )
        return {"message": "Seat successfully unlocked."}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
