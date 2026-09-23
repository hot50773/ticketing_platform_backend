import logging

from app.cache.event_layout import EventLayoutCache
from app.enums import PaymentStatus
from app.repositories.event import EventRepository
from app.repositories.order import OrderRepository
from app.schemas import Page, PaginationParams
from app.schemas.event import (
    OrderDetailResponse,
    OrderResponse,
)
from app.schemas.order import OrderCreate, OrderPay
from app.services.mappers import EventSchemaMapper
from app.services.mappers.order import OrderSchemaMapper
from app.services.validators.order import OrderValidator

logger = logging.getLogger(__name__)

class PaymentGateway:
    pass

class OrderService:
    """Orchestrates the lifecycle of an event."""

    def __init__(
        self,
        event_repository: EventRepository,
        order_repository: OrderRepository,
        event_layout_cache: EventLayoutCache,
        order_validator: OrderValidator,
        payment_gateway: PaymentGateway,
        order_schema_mapper: OrderSchemaMapper,
    ):
        self._repository = order_repository
        self._event_repository = event_repository
        self._payment_gateway = payment_gateway
        self._validator = order_validator
        self._mapper = order_schema_mapper
        self._cache = event_layout_cache

    async def create_order(self, payload: OrderCreate) -> OrderDetailResponse:
        self._validator.validate_create_order(payload)
        async with self._repository.create(payload) as order_aggr:
            return self._mapper.to_order_detail(order_aggr.order)

    async def list_orders(self, pagination: PaginationParams) -> Page[OrderResponse]:
        paged_list = await self._repository.get_orders(pagination)
        return self._mapper.to_order_page(paged_list)

    async def get_order(self, order_id: int) -> OrderDetailResponse:
        async with self._repository.load(order_id) as order_aggr:
            return self._mapper.to_order_detail(order_aggr.order)

    async def cancel_order(self, order_id: int) -> OrderResponse:
        async with self._repository.load_for_update(order_id) as order_aggr:
            self._validator.validate_cancellable(order_aggr)

            await order_aggr.cancel_order()

            return self._mapper.to_order(order_aggr)

    async def execute_payment(self, order_id: int, payload: OrderPay) -> OrderResponse:
        async with self._repository.load_for_update(order_id) as order:
            self._validator.validate_payable(order)
            await order.mark_as_paying(payload)

        # TODO: mock payment
        await self._payment_gateway.charge(payload)
        try:
            async with self._repository.load_for_update(order_id) as order:
                await order.mark_as_paid()
                return self._mapper.to_order(order)
        except Exception as e:
                await order.mark_as_payment_fail()
                raise e

    async def delete_order(self, order_id: int, payload: OrderDelete) -> None:
        async with self._repository.load_for_update(order_id) as order:
            self._validator.validate_deletable(order)
            await order.soft_delete(order_id, payload)
