"""User CRUD API.

Domain errors raised by the Service are translated to HTTP status codes by the
exception handlers registered in `app.main`, so handlers stay free of
try/except boilerplate.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from dependency_injector.wiring import inject, Provide

from app.containers import Container
from app.services.user import UserService
from app.schemas import Page, PaginationParams
from app.schemas.user import (
    UserCreate,
    UserResponse,
)

router = APIRouter(prefix="/users", tags=["users"])


# --------------------------------------------------------------------------- #
# User
# --------------------------------------------------------------------------- #
@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_user(
    payload: UserCreate,
    user_service: UserService = Depends(Provide[Container.user_service]),
):
    """Creates an unpublished user and clones the venue's zone/seat layout."""
    return await user_service.create_user(payload)


@router.get("", response_model=Page[UserResponse], status_code=status.HTTP_200_OK)
@inject
async def list_users(
    pagination: Annotated[PaginationParams, Query()],
    user_service: UserService = Depends(Provide[Container.user_service]),
):
    """Lists non-deleted users, one page at a time."""
    return await user_service.list_users(pagination)

#
# @router.get("/{user_id}", response_model=UserDetailResponse, status_code=status.HTTP_200_OK)
# @inject
# async def get_user(
#     user_id: int,
#     user_service: UserService = Depends(Provide[Container.user_service]),
# ):
#     """Gets an user with its zones and seats."""
#     return await user_service.get_user(user_id)
