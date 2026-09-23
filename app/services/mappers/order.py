"""Translation between the Event aggregate's ORM rows and its API schemas.

Kept out of the services so they read as pure use-case orchestration: the
services decide *what* happens, this decides *what it looks like* on the way
out. Rule inputs are not here - the aggregate exposes those itself. Every field is written out rather than auto-copied, so the API contract
stays greppable and an accidental model change cannot silently leak a column.
"""
from app.models import Order, OrderTicket
from app.schemas import Page
from app.schemas.order import (
    OrderResponse,
    OrderDetailResponse,
    OrderTicketResponse,
    OrderTicketDetailResponse,
)

class OrderSchemaMapper:
    """Maps Order / OrderTicket rows to API schemas and rule inputs."""

    @staticmethod
    def to_order(order: Order) -> OrderResponse:
        return OrderResponse(
            id=order.id,
            created_at=order.created_at,
            updated_at=order.updated_at,
            user_id=order.user_id,
            event_id=order.event_id,
            checkout_amount=order.checkout_amount,
            payment_deadline=order.payment_deadline,
            comment=order.comment,
            deleted_at=order.deleted_at,
        )

    def to_order_page(self, page: Page[Order]) -> Page[OrderResponse]:
        return page.map(self.to_order)

    def to_order_detail(self, order: Order) -> OrderDetailResponse:
        return OrderDetailResponse(
            **self.to_order(order).model_dump(),
            tickets=[self.to_ticket_detail(ticket) for ticket in order.tickets],
        )

    @staticmethod
    def to_ticket(ticket: OrderTicket) -> OrderTicketResponse:
        return OrderTicketResponse(
            id=ticket.id,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            event_seat_id=ticket.event_seat_id,
            amount=ticket.amount
        )

    @staticmethod
    def to_ticket_detail(ticket: OrderTicket) -> OrderTicketDetailResponse:
        return OrderTicketDetailResponse(
            id=ticket.id,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            amount=ticket.amount,
            serial_number=ticket.event_seat.serial_number,
            row=ticket.event_seat.row,
            col=ticket.event_seat.col,
            zone_name=ticket.event_seat.event_zone.name,
            event_seat_id=ticket.event_seat_id,
            event_zone_id=ticket.event_seat.event_zone_id,
        )
