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
from typing import Protocol

from redis import RedisError


from app.aggregates.order import OrderAggregate
from app.cache.event_layout import EventLayoutCache
from app.exceptions import ORDER_NOT_DELETABLE_MSG, OrderNotDeletableError
from app.queue.delayed.process_expired_order import OrderExpirationQueue
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

SEATS_STILL_LOCKED_MSG = (
    "Seats are still locked in Redis for event %s - (zone, serial) %s. No "
    "order exists for these seats, so nobody can buy them until the cleanup "
    "job frees them."
)


class PaymentGateway(Protocol):
    async def charge(self, order_id: int, amount: int, payload: OrderPay) -> str:
        """Returns the provider's transaction id; raises on a failed charge."""
        ...

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
        order_validator: OrderValidator,
        order_calculator: OrderCalculator,
        order_schema_mapper: OrderSchemaMapper,
        payment_gateway: PaymentGateway,
        event_layout_cache: EventLayoutCache,
        order_expiration_scheduler: OrderExpirationQueue,
    ):
        self._repository = order_repository
        self._payment_gateway = payment_gateway
        self._cache = event_layout_cache
        self._validator = order_validator
        self._calculator = order_calculator
        self._mapper = order_schema_mapper
        self._order_expiration_scheduler = order_expiration_scheduler

    async def create_order(self, payload: OrderCreate, customer_id: int) -> OrderDetailResponse:
        locked_seats = None
        try:
            async with self._repository.load_for_create(payload) as order_aggr_factory:
                self._validator.validate_event_orderable(order_aggr_factory.event_state)
                seats_data = await order_aggr_factory.seats_pricing()
                self._validator.validate_seats_input(payload, seats_data)
                input_data = self._calculator.calculate_order_price(customer_id, payload, seats_data)

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

    async def list_orders(self, pagination: PaginationParams, customer_id: int) -> Page[OrderResponse]:
        """One page of the customer's non-deleted orders, headers only."""
        page = await self._repository.get_orders(pagination, customer_id)
        return self._mapper.to_order_page(page)

    async def get_order(self, order_id: int, customer_id: int) -> OrderDetailResponse:
        """Returns an order with its tickets and their seat coordinates."""
        async with self._repository.load(order_id, customer_id) as order_aggr:
            return self._mapper.to_order_detail(order_aggr.with_tickets())

    async def cancel_order(self, order_id: int, customer_id: int) -> OrderResponse:
        async with self._repository.load_for_release(order_id, customer_id) as order_aggr:
            self._validator.validate_cancellable(order_aggr.state)
            order_data = order_aggr.with_tickets()
            await order_aggr.check_and_release_seats()
            await order_aggr.cancel_order()
            response = self._mapper.to_order(order_aggr.order)

        await self._release_cached_seats(order_data)
        return response

    async def check_order_expiration(self, order_id: int) -> None:
        order_data = None
        async with self._repository.load_for_release(order_id) as order_aggr:
            if self._validator.check_deletable(order_aggr.state):
                order_data = await self._release_and_soft_delete(
                    order_aggr, OrderDelete(reason="RESERVATION EXPIRED WITHOUT PAYMENT"), manager_id=None
                )
        if order_data is not None:
            await self._release_cached_seats(order_data)

    async def delete_order(self, order_id: int, payload: OrderDelete, manager_id: int) -> None:
        async with self._repository.load_for_release(order_id) as order_aggr:
            if not self._validator.check_deletable(order_aggr.state):
                raise OrderNotDeletableError(ORDER_NOT_DELETABLE_MSG.format(order_id=order_id))
            order_data = await self._release_and_soft_delete(order_aggr, payload, manager_id)

        await self._release_cached_seats(order_data)

    @staticmethod
    async def _release_and_soft_delete(
        order_aggr: OrderAggregate, payload: OrderDelete, manager_id: int | None
    ) -> OrderWithTicketsInternal:
        order_data = order_aggr.with_tickets()
        await order_aggr.check_and_release_seats()
        await order_aggr.soft_delete_cascade(payload, manager_id)
        return order_data

    async def _release_cached_seats(self, order_data: OrderWithTicketsInternal) -> None:
        # TODO: a failure here leaves the bits set while Postgres says AVAILABLE;
        #  rebuilding the sold bitmap from Postgres would recover the seats.
        await self._cache.release_seats(
            event_id=order_data.order.event_id,
            seats=[
                SeatPositionInternal(
                    event_zone_id=ticket.event_zone_id,
                    serial_number=ticket.serial_number,
                )
                for ticket in order_data.tickets
            ],
        )

    async def execute_payment(
        self, order_id: int, payload: OrderPay, customer_id: int
    ) -> OrderResponse:
        async with self._repository.load_for_update(order_id, customer_id) as order_aggr:
            self._validator.validate_payable(order_aggr.state)
            amount = order_aggr.order.checkout_amount
            await order_aggr.mark_as_paying(payload)

        try:
            transaction_id = await self._payment_gateway.charge(order_id, amount, payload)
        except Exception:
            # TODO: timeout 不代表扣款失敗，直接標 FAILED 會讓客人能再付一次而被重複扣款
            logger.exception("Charge failed for order %s; releasing the claim", order_id)
            async with self._repository.load_for_update(order_id) as order_aggr:
                await order_aggr.mark_as_payment_fail()
            raise

        logger.info("Order %s charged, provider transaction %s", order_id, transaction_id)
        async with self._repository.load_for_update(order_id) as order_aggr:
            await order_aggr.mark_as_paid()
            return self._mapper.to_order(order_aggr.order)
