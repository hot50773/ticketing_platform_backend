"""Order lifecycle use cases: create, read, pay, cancel, release.

Every use case enters the repository exactly once per transaction, through an
aggregate that owns it. Reading state and writing the decision that depends on
it therefore happen together; see app/aggregates/order.py for why the write
paths also take a row lock.

Boundaries:
  - persistence goes through the aggregate, never a session
  - domain rules go through OrderValidator, which stays I/O free
  - ORM -> schema translation goes through OrderSchemaMapper
  - the payment provider sits behind PaymentGateway, so nothing here knows
    whether a charge is a network call, a mock, or a queue

execute_payment is the one use case that deliberately spans more than one
transaction - see its docstring.
"""
import logging
from typing import Protocol

from app.repositories.order import OrderRepository
from app.schemas import Page, PaginationParams
from app.schemas.order import (
    OrderCreate,
    OrderDelete,
    OrderDetailResponse,
    OrderPay,
    OrderResponse,
)
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
        order_validator: OrderValidator,
        order_schema_mapper: OrderSchemaMapper,
        payment_gateway: PaymentGateway,
    ):
        self._repository = order_repository
        self._validator = order_validator
        self._mapper = order_schema_mapper
        self._payment_gateway = payment_gateway

    async def create_order(self, payload: OrderCreate) -> OrderDetailResponse:
        """flow: validate the payload -> insert order + tickets.

        The order is always created PENDING / PENDING. Seat availability is not
        checked here yet - see the TODO in the module notes below.
        """
        self._validator.validate_create_order(payload)
        async with self._repository.create(payload) as order_aggr:
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
        async with self._repository.load_for_update(order_id) as order_aggr:
            self._validator.validate_deletable(order_aggr.state)
            await order_aggr.soft_delete(payload)

    async def execute_payment(self, order_id: int, payload: OrderPay) -> OrderResponse:
        """flow: claim -> charge -> settle, as three separate transactions.

        Splitting them is the point, not an accident. A database transaction
        must not stay open across a call to a payment provider: the call can
        hang for as long as the provider's timeout, and holding `FOR UPDATE` on
        the order for that whole window would pile up every retry behind it.

        Each step therefore commits on its own:

          1. claim   - assert payable, write IN_PROGRESS, commit. A second
                       request now finds IN_PROGRESS and is rejected, which is
                       what stops a double charge.
          2. charge  - no transaction held. On failure, a compensating
                       transaction writes FAILED so the customer can retry.
          3. settle  - write SUCCESS / CONFIRMED.

        The gap this leaves: a crash between 2 and 3 strands the order at
        IN_PROGRESS with the money taken. That is not recoverable from inside
        this request - it needs a reconciliation job that asks the provider
        what actually happened. Left explicit here rather than pretended away.
        """
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


# TODO (not interface - business decisions still open):
#   - Seat availability. create_order writes tickets without checking whether
#     the seats are free; the partial unique index stops the same seat being
#     sold twice within one order, not across orders. That check belongs on
#     the Redis bitmap being built in app/cache/event_layout.py.
#   - "Event still on sale" rule. OrderValidator.validate_create_order can only
#     see the payload; the event-state rule needs EventRepository to read the
#     event and hand an *Internal DTO to a sibling validator method.
#   - Expiry. Nothing releases an order whose payment_deadline has passed.
#     app/jobs/ is where that scheduler would live, calling delete_order.
