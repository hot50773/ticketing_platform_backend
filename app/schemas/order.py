"""API contracts for the Order aggregate (Order).

Field names follow the ORM models (`start_at`, `end_at`, `col`).
Schemas suffixed `Internal` never cross the API boundary: they are the
transport objects between Repository and Service (no dict / tuple passing).

Seat lists are deliberately kept out of the event/zone responses: a 萬人 venue
has 10k+ OrderSeat rows, so the layout is fetched per zone through its own
endpoint instead.
"""
from datetime import datetime

from sqlmodel import SQLModel, Field

from ._shared import _TableMetaMixin


# --------------------------------------------------------------------------- #
# Requests - Order
# --------------------------------------------------------------------------- #

class OrderTicketCreate(SQLModel):
    event_seat_id: int
    amount: int = Field(description="票價")

class OrderCreate(SQLModel):
    user_id: int
    event_id: int
    checkout_amount: int = Field(description="結帳金額")
    payment_deadline: datetime | None
    comment: str | None = None
    tickets: list[OrderTicketCreate] = Field(default_factory=list)

# --------------------------------------------------------------------------- #
# Requests - OrderPay
# --------------------------------------------------------------------------- #

class OrderPay(SQLModel):
    order_id: int
    payment_method: str = Field(max_length=100)
# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #

class OrderResponse(SQLModel, _TableMetaMixin):
    user_id: int
    event_id: int
    checkout_amount: int = Field(description="結帳金額")
    payment_deadline: datetime | None
    comment: str | None = None
    deleted_at: datetime | None = None

class OrderTicketResponse(SQLModel, _TableMetaMixin):
    event_seat_id: int
    amount: int = Field(description="票價")

class OrderTicketDetailResponse(SQLModel, _TableMetaMixin):
    event_seat_id: int
    event_zone_id: int
    amount: int = Field(description="票價")
    row: int
    col: int
    serial_number: int
    zone_name: str

class OrderDetailResponse(OrderResponse):
    tickets: list[OrderTicketDetailResponse] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Internal transport (Repository -> Service)
# --------------------------------------------------------------------------- #
# class OrderZoneWithSeatCountInternal(SQLModel):
#     zone: OrderZone
#     seat_count: int
