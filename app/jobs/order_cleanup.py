from app.jobs._base import BaseJob
from app.services.event import EventService
from dependency_injector.wiring import inject, Provide
from app.containers import Container
import logging

logger = logging.getLogger(__name__)

class OrderCleanupJob(BaseJob):
    CLEANUP_INTERVAL_MINUTES = 1

    @inject
    def __init__(self, event_service: EventService = Provide[Container.event_service]):
        self._event_service = event_service

    @property
    def job_id(self) -> str:
        return "order_cleanup_job"

    @property
    def trigger_type(self) -> str:
        return "interval"

    @property
    def trigger_args(self) -> dict:
        return {"minutes": self.CLEANUP_INTERVAL_MINUTES}


    async def run(self) -> None:
        logger.info("Executing scheduled task: releasing expired orders...")
        try:
            await self._event_service.release_expired_orders()
        except Exception as e:
            logger.error(f"Error in OrderCleanupJob: {e}")
