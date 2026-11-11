from taskiq import TaskiqEvents, TaskiqState

from app.containers import Container
from app.workers import broker


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _startup(state: TaskiqState) -> None:
    state.container = Container()


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _shutdown(state: TaskiqState) -> None:
    await state.container.db().close_all_sessions()
    await state.container.redis_client().shutdown()
