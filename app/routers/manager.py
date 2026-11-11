"""Admin-only: adding, listing and (de)activating manager accounts."""
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, status

from app.containers import Container
from app.enums import ManagerRole
from app.routers._auth import Admin
from app.schemas import Page
from app.schemas.manager import ManagerCreate
from app.schemas.manager import ManagerListParams, ManagerResponse
from app.services.manager import ManagerService

admin_router = APIRouter(prefix="/admin/managers", tags=["managers"])


@admin_router.post("", response_model=ManagerResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_manager(
    payload: ManagerCreate,
    _: Admin,
    manager_service: ManagerService = Depends(Provide[Container.manager_service]),
):
    return await manager_service.create_manager(payload, ManagerRole.MANAGER)


@admin_router.get("", response_model=Page[ManagerResponse], status_code=status.HTTP_200_OK)
@inject
async def list_managers(
    params: Annotated[ManagerListParams, Query()],
    _: Admin,
    manager_service: ManagerService = Depends(Provide[Container.manager_service]),
):
    """Lists admins and managers one page at a time, optionally of one role."""
    return await manager_service.list_managers(params)


@admin_router.post("/{manager_id}/deactivate", response_model=ManagerResponse, status_code=status.HTTP_200_OK)
@inject
async def deactivate_manager(
    manager_id: int,
    _: Admin,
    manager_service: ManagerService = Depends(Provide[Container.manager_service]),
):
    """Locks the manager out at once, including tokens already issued."""
    return await manager_service.set_manager_active(manager_id, is_active=False)


@admin_router.post("/{manager_id}/activate", response_model=ManagerResponse, status_code=status.HTTP_200_OK)
@inject
async def activate_manager(
    manager_id: int,
    _: Admin,
    manager_service: ManagerService = Depends(Provide[Container.manager_service]),
):
    return await manager_service.set_manager_active(manager_id, is_active=True)
