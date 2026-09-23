"""API contracts for the Order aggregate (Order / OrderTicket).

Field names follow the ORM models.
Schemas suffixed `Internal` never cross the API boundary: they are the
transport objects between Repository and Service (no dict / tuple passing).

An order carries its tickets inline: unlike an event layout, a single order is
a handful of seats, not 10k, so there is no reason to page them separately.
"""
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator
from sqlmodel import SQLModel, Field

from app.enums import OrderStatus, PaymentStatus
from app.models import Order
from ._shared import UtcDatetime, _TableMetaMixin


# --------------------------------------------------------------------------- #
# Requests - Order
# --------------------------------------------------------------------------- #

class OrderTicketCreate(SQLModel):
    """One seat being bought, with the price it is being bought at.

    The amount is carried per ticket rather than read from the zone, because a
    price change after the order is placed must not retroactively alter what
    the customer agreed to pay.
    """
    event_seat_id: int
    amount: int = Field(ge=0, description="票價")


def _reject_duplicate_seats(tickets: list[OrderTicketCreate]) -> list[OrderTicketCreate]:
    """A seat is bought once per order - the partial unique index on
    (order_id, event_seat_id) says the same thing at the database."""
    seat_ids = [ticket.event_seat_id for ticket in tickets]
    if len(seat_ids) != len(set(seat_ids)):
        raise ValueError("duplicate event_seat_id in tickets")
    return tickets


OrderTickets = Annotated[list[OrderTicketCreate], AfterValidator(_reject_duplicate_seats)]


class OrderCreate(SQLModel):
    """POST /orders.

    An empty order is rejected at the boundary rather than by a rule: it is a
    malformed request, not a business-rule violation, so it earns a 422.
    """
    user_id: int
    event_id: int
    checkout_amount: int = Field(ge=0, description="結帳金額")
    payment_deadline: UtcDatetime | None = None
    comment: str | None = Field(default=None, max_length=500)
    tickets: OrderTickets = Field(min_length=1)

# --------------------------------------------------------------------------- #
# Requests - OrderPay
# --------------------------------------------------------------------------- #

class OrderPay(SQLModel):
    """POST /orders/{order_id}/pay."""
    payment_method: str = Field(max_length=100)


class OrderDelete(SQLModel):
    """DELETE /orders/{order_id}.

    A reason is required: soft-deleting an order is an operator action that
    someone will have to explain later, and the audit trail is worth more than
    the convenience of an empty body.
    """
    reason: str = Field(max_length=500, description="刪除原因，寫入 comment 供日後稽核")
# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #

class OrderResponse(SQLModel, _TableMetaMixin):
    """An order header. status and payment_status are both here on purpose:
    they move independently - a PENDING order can have a FAILED payment behind
    it and still be retried - so collapsing them into one field would lose the
    distinction the pay and cancel rules turn on."""
    user_id: int
    event_id: int
    status: OrderStatus
    payment_status: PaymentStatus
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
class OrderStateInternal(SQLModel):
    """Everything the rules need to judge an order, and nothing else.

    The aggregate hands this to OrderValidator instead of itself, for the same
    reason EventAggregate hands over EventStatusInternal: a rule that can only
    be exercised by constructing an aggregate needs a database to unit-test.
    """
    id: int
    status: OrderStatus
    payment_status: PaymentStatus
    payment_deadline: datetime | None


class OrderTicketInternal(SQLModel):
    """One ticket with the seat coordinates already resolved.

    Built by the aggregate from the eager-loaded relationship chain, so the
    mapper never walks ORM relationships itself and can never trigger a lazy
    load from inside a response.
    """
    id: int
    created_at: datetime
    updated_at: datetime
    event_seat_id: int
    event_zone_id: int
    zone_name: str
    row: int
    col: int
    serial_number: int
    amount: int


class OrderWithTicketsInternal(SQLModel):
    """An order together with its resolved tickets - what a detail read returns."""
    order: "Order"
    tickets: list[OrderTicketInternal] = Field(default_factory=list)
