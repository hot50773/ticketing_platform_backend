import logging
from datetime import datetime
from typing import Annotated

from dependency_injector.wiring import inject, Provide
from taskiq import Context, TaskiqDepends

# from app.containers import Container
from app.workers import broker, schedule_source

logger = logging.getLogger(__name__)


@broker.task(task_name="check_order_expiration_task")
async def check_order_expiration_task(
    order_id: int, context: Annotated[Context, TaskiqDepends()],
) -> str:
    logger.info("觸發超時刪單任務，檢查訂單 ID: %s", order_id)
    order_service = context.state.container.order_service()
    await order_service.check_order_expiration(order_id)

    return f"Expired Order {order_id} processed."


# @broker.task(task_name="check_order_expiration_task")
# @inject
# async def check_order_expiration_task(
#     order_id: int, order_service = TaskiqDepends(Provide[Container.order_service])
# ) -> str:
#     logger.info("觸發超時刪單任務，檢查訂單 ID: %s", order_id)
#     await order_service.check_order_expiration(order_id)
#
#     return f"Expired Order {order_id} processed."


class OrderExpirationQueue:
    """Hands an order to the worker at its payment deadline."""

    @staticmethod
    async def schedule(order_id: int, schedule_time: datetime) -> None:
        await check_order_expiration_task.schedule_by_time(
            schedule_source, schedule_time, order_id=order_id
        )
