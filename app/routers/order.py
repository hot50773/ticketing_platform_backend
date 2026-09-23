"""Event CRUD API.

Domain errors raised by the Service are translated to HTTP status codes by the
exception handlers registered in `app.main`, so handlers stay free of
try/except boilerplate.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from dependency_injector.wiring import inject, Provide

from app.containers import Container
from app.services.order import OrderService
from app.schemas import Page, PaginationParams
from app.schemas.order import (
    OrderDetailResponse,
    OrderResponse,
    OrderPay,
    OrderCreate,
)

router = APIRouter(prefix="/orders", tags=["orders"])
"""
# TODO: apis
  建立訂單 POST /orders
  付款 POST /orders/{order_id}/pay
  取消訂單 POST /orders/{order_id}/cancel
  # customer
  查看訂單列表 GET /orders/list
  查看訂單詳情 /orders/{order_id}
"""

# --------------------------------------------------------------------------- #
# Order
# --------------------------------------------------------------------------- #

@router.post("", response_model=OrderDetailResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_order(
    payload: OrderCreate,
    order_service: OrderService = Depends(Provide[Container.order_service]),
):
    """Creates an unpublished event and clones the venue's zone/seat layout."""
    return await order_service.create_order(payload)


@router.get("", response_model=Page[OrderResponse], status_code=status.HTTP_200_OK)
@inject
async def list_orders(
    pagination: Annotated[PaginationParams, Query()],
    order_service: OrderService = Depends(Provide[Container.order_service]),
):
    """Lists non-deleted orders, one page at a time."""
    return await order_service.list_orders(pagination)


@router.get("/{order_id}", response_model=OrderDetailResponse, status_code=status.HTTP_200_OK)
@inject
async def get_order(
    order_id: int,
    order_service: OrderService = Depends(Provide[Container.order_service]),
):
    """Gets an event with its zones and seats."""
    return await order_service.get_order(order_id)


@router.post("/{order_id}/pay", response_model=OrderResponse, status_code=status.HTTP_200_OK)
@inject
async def pay_order(
    order_id: int,
    payload: OrderPay,
    order_service: OrderService = Depends(Provide[Container.order_service]),
):
    """Updates the event's holding time. Rejected once the event is published."""
    return await order_service.execute_payment(order_id, payload)


@router.post("/{order_id}/cancel", response_model=OrderResponse, status_code=status.HTTP_200_OK)
@inject
async def cancel_order(
    order_id: int,
    order_service: OrderService = Depends(Provide[Container.order_service]),
):
    return await order_service.cancel_order(order_id)
