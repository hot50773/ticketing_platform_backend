"""Order API.

Domain errors raised by the Service are translated to HTTP status codes by the
exception handlers registered in `app.main`, so handlers stay free of
try/except boilerplate.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from dependency_injector.wiring import inject, Provide

from app.containers import Container
from app.routers._auth import AnyManager, Customer, get_current_manager
from app.services.order import OrderService
from app.schemas import Page, PaginationParams
from app.schemas.order import (
    OrderCreate,
    OrderDelete,
    OrderDetailResponse,
    OrderPay,
    OrderResponse,
)

router = APIRouter(prefix="/orders", tags=["orders"])
admin_router = APIRouter(
    prefix="/admin/orders", tags=["orders"], dependencies=[Depends(get_current_manager)]
)

# --------------------------------------------------------------------------- #
# Order
# --------------------------------------------------------------------------- #

@router.post("", response_model=OrderDetailResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_order(
    payload: OrderCreate,
    customer: Customer,
    order_service: OrderService = Depends(Provide[Container.order_service]),
):
    """Creates a pending order with its tickets."""
    return await order_service.create_order(payload, customer.id)


@router.get("", response_model=Page[OrderResponse], status_code=status.HTTP_200_OK)
@inject
async def list_orders(
    pagination: Annotated[PaginationParams, Query()],
    customer: Customer,
    order_service: OrderService = Depends(Provide[Container.order_service]),
):
    """Lists the customer's non-deleted orders, one page at a time."""
    return await order_service.list_orders(pagination, customer.id)


@router.get("/{order_id}", response_model=OrderDetailResponse, status_code=status.HTTP_200_OK)
@inject
async def get_order(
    order_id: int,
    customer: Customer,
    order_service: OrderService = Depends(Provide[Container.order_service]),
):
    """Gets an order with its tickets and their seat coordinates."""
    return await order_service.get_order(order_id, customer.id)


@router.post("/{order_id}/pay", response_model=OrderResponse, status_code=status.HTTP_200_OK)
@inject
async def pay_order(
    order_id: int,
    payload: OrderPay,
    customer: Customer,
    order_service: OrderService = Depends(Provide[Container.order_service]),
):
    """Pays for the order. Rejected once it is cancelled, already paid, has a
    charge in flight, or has passed its payment deadline."""
    return await order_service.execute_payment(order_id, payload, customer.id)


@router.post("/{order_id}/cancel", response_model=OrderResponse, status_code=status.HTTP_200_OK)
@inject
async def cancel_order(
    order_id: int,
    customer: Customer,
    order_service: OrderService = Depends(Provide[Container.order_service]),
):
    """Cancels the order. The row survives - a paid order cannot be
    cancelled, and a cancelled one still blocks the event from unpublishing."""
    return await order_service.cancel_order(order_id, customer.id)


@admin_router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_order(
    order_id: int,
    payload: OrderDelete,
    manager: AnyManager,
    order_service: OrderService = Depends(Provide[Container.order_service]),
):
    """Releases the order: soft-deletes it with its tickets, freeing the seats.
    Rejected once the order is paid for."""
    await order_service.delete_order(order_id, payload, manager.id)
