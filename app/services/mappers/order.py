"""Translation between the Order aggregate's rows and its API schemas.

Kept out of the services so they read as pure use-case orchestration: the
services decide *what* happens, this decides *what it looks like* on the way
out. Every field is written out rather than auto-copied, so the API contract
stays greppable and an accidental model change cannot silently leak a column.

Ticket details arrive as OrderTicketInternal, already flattened by the
aggregate. This mapper deliberately never walks an ORM relationship: the models
are lazy="raise", and a traversal from here would run after the aggregate's
transaction has closed.
"""
from app.models import Order
from app.schemas import Page
from app.schemas.order import (
    OrderDetailResponse,
    OrderResponse,
    OrderTicketDetailResponse,
    OrderTicketInternal,
    OrderWithTicketsInternal,
)


class OrderSchemaMapper:
    """Maps Order / OrderTicket rows to API schemas."""

    @staticmethod
    def to_order(order: Order) -> OrderResponse:
        return OrderResponse(
            id=order.id,
            created_at=order.created_at,
            updated_at=order.updated_at,
            user_id=order.user_id,
            event_id=order.event_id,
            status=order.status,
            payment_status=order.payment_status,
            checkout_amount=order.checkout_amount,
            payment_deadline=order.payment_deadline,
            comment=order.comment,
            deleted_at=order.deleted_at,
        )

    def to_order_page(self, page: Page[Order]) -> Page[OrderResponse]:
        return page.map(self.to_order)

    def to_order_detail(self, layout: OrderWithTicketsInternal) -> OrderDetailResponse:
        return OrderDetailResponse(
            **self.to_order(layout.order).model_dump(),
            tickets=[self.to_ticket_detail(ticket) for ticket in layout.tickets],
        )

    @staticmethod
    def to_ticket_detail(ticket: OrderTicketInternal) -> OrderTicketDetailResponse:
        return OrderTicketDetailResponse(
            id=ticket.id,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            event_seat_id=ticket.event_seat_id,
            event_zone_id=ticket.event_zone_id,
            zone_name=ticket.zone_name,
            row=ticket.row,
            col=ticket.col,
            serial_number=ticket.serial_number,
            amount=ticket.amount,
        )
