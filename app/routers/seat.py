from fastapi import APIRouter, Depends, Query, HTTPException
from dependency_injector.wiring import inject, Provide
from app.containers import Container
from app.services.autocomplete import AutocompleteService

router = APIRouter(prefix="/seat", tags=["seat"])

@router.get("")
@inject
async def lock_ticket(
    q: str = Query(..., min_length=1, description="The prefix string to search for suggestions"),
    autocomplete_service: AutocompleteService = Depends(Provide[Container.autocomplete_service])
):
    """Fetches autocomplete suggestions matching query prefix from Redis suggestion index."""
    try:
        ...
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Autocomplete search failed due to internal cache error: {str(e)}"
        )
