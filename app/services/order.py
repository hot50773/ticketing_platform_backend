"""Order lifecycle use cases: create, read, pay, cancel, release.

Boundaries:
  - persistence goes through the aggregate, never a session
  - domain rules go through OrderValidator, which stays I/O free
  - prices and deadlines go through OrderCalculator, which stays I/O free
  - ORM -> schema translation goes through OrderSchemaMapper
  - the payment provider sits behind PaymentGateway, so nothing here knows
    whether a charge is a network call, a mock, or a queue
"""
import logging
from datetime import datetime
from typing import Protocol

from redis import RedisError


from app.cache.event_layout import EventLayoutCache
from app.models import Order
from app.queue.delayed.process_expired_order import OrderExpirationQueue
from app.repositories.event import EventRepository
from app.repositories.order import OrderRepository
from app.schemas.event import EventSeatsPricingInternal, SeatPositionInternal
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

# The only failure that leaves seats locked in Redis with no order in Postgres
# to find them by, so this line is the only record of which seats are stuck.
SEATS_STILL_LOCKED_MSG = (
    "Seats are still locked in Redis for event %s - (zone, serial) %s. No "
    "order exists for these seats, so nobody can buy them until the cleanup "
    "job frees them."
)


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

class OrderScheduler:
    """Runs check_order_expiration for an order once its deadline passes."""

    @staticmethod
    async def schedule_order_expiration_task(order_id: int, deadline: datetime) -> None:
        await OrderExpirationQueue.schedule(order_id=order_id, schedule_time=deadline)


class UnimplementedPaymentGateway:
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
        order_expiration_scheduler: OrderExpirationQueue,
    ):
        self._repository = order_repository
        self._event_repository = event_repository
        self._payment_gateway = payment_gateway
        self._cache = event_layout_cache
        self._validator = order_validator
        self._calculator = order_calculator
        self._mapper = order_schema_mapper
        self._order_expiration_scheduler = order_expiration_scheduler

    async def create_order(self, payload: OrderCreate) -> OrderDetailResponse:
        locked_seats = None
        try:
            async with self._repository.load_for_create(payload) as order_aggr_factory:
                self._validator.validate_event_orderable(order_aggr_factory.event_state)
                seats_data = await order_aggr_factory.seats_pricing()
                self._validator.validate_seats_input(payload, seats_data)
                input_data = self._calculator.calculate_order_price(payload, seats_data)

                await self._cache.check_and_lock_seats_atomic(seats_data)
                locked_seats = seats_data
                order_aggr = await order_aggr_factory.create(input_data)
                order_data = order_aggr.with_tickets()
        except BaseException as e:
            if locked_seats is not None:
                await self._undo_seat_lock(locked_seats)
            raise e

        if order_data.order.payment_deadline is not None:
            await self._order_expiration_scheduler.schedule(
                order_data.order.id, order_data.order.payment_deadline
            )
        return self._mapper.to_order_detail(order_data)

    async def _undo_seat_lock(
        self, event_seats_pricing: EventSeatsPricingInternal
    ) -> None:
        """Unlocks seats whose order was never created.

        A Redis outage is logged and swallowed - it must not replace the error
        that caused it. Anything else is a defect of ours and surfaces.
        """
        event_id = event_seats_pricing.event_id
        seats = [
            SeatPositionInternal(
                event_zone_id=seat.event_zone_id,
                serial_number=seat.seat_serial_number,
            )
            for seat in event_seats_pricing.seats
        ]
        stuck_seats = [(seat.event_zone_id, seat.serial_number) for seat in seats]

        try:
            await self._cache.release_seats(event_id=event_id, seats=seats)
        except RedisError:
            logger.exception(SEATS_STILL_LOCKED_MSG, event_id, stuck_seats)

    async def list_orders(self, pagination: PaginationParams) -> Page[OrderResponse]:
        """One page of non-deleted orders, headers only."""
        page = await self._repository.get_orders(pagination)
        return self._mapper.to_order_page(page)

    async def get_order(self, order_id: int) -> OrderDetailResponse:
        """Returns an order with its tickets and their seat coordinates."""
        async with self._repository.load(order_id) as order_aggr:
            return self._mapper.to_order_detail(order_aggr.with_tickets())

    # async def cancel_order(self, order_id: int) -> OrderResponse:
    #     """flow: assert cancellable -> mark CANCELLED.
    #
    #     The row survives. A cancelled order still counts against unpublishing
    #     the event; only a released (soft-deleted) one stops counting.
    #     """
    #     async with self._repository.load_for_update(order_id) as order_aggr:
    #         self._validator.validate_cancellable(order_aggr.state)
    #         await order_aggr.cancel_order()
    #         return self._mapper.to_order(order_aggr.order)

    async def check_order_expiration(self, order_id: int) -> None:
        async with self._repository.load_for_update(order_id) as order_aggr:
            should_run = self._validator.check_deletable(order_aggr.state)
            if should_run:
                await order_aggr.soft_delete_cascade(OrderDelete(reason="RESERVATION EXPIRED WITHOUT PAYMENT"))

        if should_run:
            await self.release_seats_by_order(order_aggr.with_tickets())

    async def delete_order(self, order_id: int, payload: OrderDelete) -> None:
        order_data = await self._delete_order(
            order_id, payload
        )
        await self.release_seats_by_order(order_data)

    async def release_seats_by_order(self, order_data: OrderWithTicketsInternal):
        event_id = order_data.order.event_id
        event_seat_ids = [ticket.event_seat_id for ticket in order_data.tickets]

        async with self._event_repository.load_for_update(event_id) as event_aggr:
            await event_aggr.check_and_release_seats(event_seat_ids)

        await self._cache.release_seats(
            event_id=event_id,
            seats=[
                SeatPositionInternal(
                    event_zone_id=ticket.event_zone_id,
                    serial_number=ticket.serial_number,
                )
                for ticket in order_data.tickets
            ],
        )

    async def _delete_order(self, order_id: int, payload: OrderDelete) -> OrderWithTicketsInternal:
        async with self._repository.load_for_update(order_id) as order_aggr:
            if not self._validator.check_deletable(order_aggr.state):
                raise Exception(f"Order {order_id} cannot be deleted")
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
