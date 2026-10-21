"""Order lifecycle use cases: create, read, pay, cancel, release.

Every use case enters the repository exactly once per transaction, through an
aggregate that owns it. Reading state and writing the decision that depends on
it therefore happen together; see app/aggregates/order.py for why the write
paths also take a row lock.

Boundaries:
  - persistence goes through the aggregate, never a session
  - domain rules go through OrderValidator, which stays I/O free
  - prices and deadlines go through OrderCalculator, which stays I/O free
  - ORM -> schema translation goes through OrderSchemaMapper
  - the payment provider sits behind PaymentGateway, so nothing here knows
    whether a charge is a network call, a mock, or a queue

execute_payment is the one use case that deliberately spans more than one
transaction - see its docstring.
"""
import logging
from typing import Protocol

from app.cache.event_layout import EventLayoutCache
from app.models import Order
from app.repositories.event import EventRepository
from app.repositories.order import OrderRepository
from app.schemas import Page, PaginationParams
from app.schemas.order import (
    OrderCreate,
    OrderDelete,
    OrderDetailResponse,
    OrderPay,
    OrderResponse,
    OrderWithTicketsInternal,
)
from app.services.calculators.order import OrderCalculator
from app.services.mappers.order import OrderSchemaMapper
from app.services.validators.order import OrderValidator

logger = logging.getLogger(__name__)


class PaymentGateway(Protocol):
    """The payment provider, as this service needs it.

    A Protocol rather than a base class: the real provider, the sandbox and the
    test double have nothing in common but this call, and nothing in this
    module should depend on which one is wired in.
    """

    async def charge(self, order_id: int, amount: int, payload: OrderPay) -> str:
        """Charges the customer and returns the provider's transaction id.

        Raises on a declined or failed charge. Must be idempotent per order_id
        if the provider supports it - execute_payment guards against concurrent
        double-charging with a row lock, but not against a retry after a
        timeout where the first call may still have gone through.
        """
        ...


class UnimplementedPaymentGateway:
    """Placeholder binding so the container resolves before a provider exists.

    Deliberately raises rather than returning a fake success: a silent no-op
    here would let an order reach CONFIRMED with no money behind it.
    """

    async def charge(self, order_id: int, amount: int, payload: OrderPay) -> str:
        raise NotImplementedError(
            "No payment provider is wired in. Bind a real PaymentGateway in "
            "app/containers.py before enabling POST /orders/{order_id}/pay."
        )


class OrderService:
    """Orchestrates the lifecycle of an order."""

    def __init__(
        self,
        order_repository: OrderRepository,
        event_repository: EventRepository,
        order_validator: OrderValidator,
        order_calculator: OrderCalculator,
        order_schema_mapper: OrderSchemaMapper,
        payment_gateway: PaymentGateway,
        event_layout_cache: EventLayoutCache,
    ):
        self._repository = order_repository
        self._event_repository = event_repository
        self._payment_gateway = payment_gateway
        self._cache = event_layout_cache
        self._validator = order_validator
        self._calculator = order_calculator
        self._mapper = order_schema_mapper

    async def create_order(self, payload: OrderCreate) -> OrderDetailResponse:
        """flow: assert the event is orderable -> resolve and assert the seats
        -> price them -> insert. Always PENDING / PENDING.

        One transaction holding `FOR SHARE` on the event, so it cannot be
        unpublished or repriced between the rules and the insert. Prices and
        the deadline come from the backend; the request only picks seats.
        """
        async with self._repository.load_for_create(payload) as order_aggr_factory:
            # The event rule is a field read and the seat resolution is a
            # query, so a closed event must be rejected before paying for it.
            self._validator.validate_event_orderable(order_aggr_factory.event_state)

            seats_pricing_data = await order_aggr_factory.seats_pricing()
            self._validator.validate_seats_input(payload, seats_pricing_data)

            await self._cache.check_and_lock_seats_atomic(seats_pricing_data)

            priced_order = self._calculator.calculate_order_price(payload, seats_pricing_data)
            order_aggr = await order_aggr_factory.create(priced_order)
            return self._mapper.to_order_detail(order_aggr.with_tickets())

    async def list_orders(self, pagination: PaginationParams) -> Page[OrderResponse]:
        """One page of non-deleted orders, headers only."""
        page = await self._repository.get_orders(pagination)
        return self._mapper.to_order_page(page)

    async def get_order(self, order_id: int) -> OrderDetailResponse:
        """Returns an order with its tickets and their seat coordinates."""
        async with self._repository.load(order_id) as order_aggr:
            return self._mapper.to_order_detail(order_aggr.with_tickets())

    async def cancel_order(self, order_id: int) -> OrderResponse:
        """flow: assert cancellable -> mark CANCELLED.

        The row survives. A cancelled order still counts against unpublishing
        the event; only a released (soft-deleted) one stops counting.
        """
        async with self._repository.load_for_update(order_id) as order_aggr:
            self._validator.validate_cancellable(order_aggr.state)
            await order_aggr.cancel_order()
            return self._mapper.to_order(order_aggr.order)

    async def delete_order(self, order_id: int, payload: OrderDelete) -> None:
        """flow: assert deletable -> soft-delete order + tickets.

        This is what releases the seats: the partial unique index on
        (order_id, event_seat_id) ignores soft-deleted rows, so the seats
        become buyable again.
        """
        order_data = await self._check_and_delete_order(order_id, payload)

        await self.release_seats_by_order(order_data)
        # TODO: 要有排程定期清票，讓 Redis 沒有正確釋放的票被處理

    async def release_seats_by_order(self, order_data: OrderWithTicketsInternal):
        event_id = order_data.order.event_id
        event_seat_ids = [ticket.event_seat_id for ticket in order_data.tickets]

        async with self._event_repository.load_for_update(event_id) as event_aggr:
            await event_aggr.check_and_release_seats(event_seat_ids)

        await self._cache.release_seats(event_id=event_id, tickets_data=order_data.tickets)

    async def _check_and_delete_order(self, order_id: int, payload: OrderDelete) -> OrderWithTicketsInternal:
        async with self._repository.load_for_update(order_id) as order_aggr:
            self._validator.validate_deletable(order_aggr.state)
            await order_aggr.soft_delete_cascade(payload)
            return order_aggr.with_tickets()

    async def execute_payment(self, order_id: int, payload: OrderPay) -> OrderResponse:
        async with self._repository.load_for_update(order_id) as order_aggr:
            self._validator.validate_payable(order_aggr.state)
            amount = order_aggr.order.checkout_amount
            await order_aggr.mark_as_paying(payload)

        try:
            transaction_id = await self._payment_gateway.charge(order_id, amount, payload)
        except Exception:
            logger.exception("Charge failed for order %s; releasing the claim", order_id)
            async with self._repository.load_for_update(order_id) as order_aggr:
                await order_aggr.mark_as_payment_fail()
            raise

        logger.info("Order %s charged, provider transaction %s", order_id, transaction_id)
        async with self._repository.load_for_update(order_id) as order_aggr:
            await order_aggr.mark_as_paid()
            return self._mapper.to_order(order_aggr.order)


# TODO
#   - 付款成功後把 EventSeat.status 推到 RESERVED，並更新 redis 的 sold bitmap
