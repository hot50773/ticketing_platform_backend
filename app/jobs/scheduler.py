from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.containers import Container
from app.config import settings
from app.jobs.registry import REGISTERED_JOB_CLASSES
import logging
import sys

logger = logging.getLogger(__name__)

IS_TESTING = "pytest" in sys.modules

def start_scheduler(container: Container) -> AsyncIOScheduler | None:
    """Registers all configured jobs and starts the scheduler if enabled."""
    # Separation of concern: toggle off scheduler if enable_cron_jobs setting is False or running tests
    if not settings.enable_cron_jobs or IS_TESTING:
        logger.info("APScheduler is disabled (either settings.enable_cron_jobs is False or running tests).")
        return None

    logger.info("Initializing APScheduler...")
    scheduler = AsyncIOScheduler()
    
    for job_class in REGISTERED_JOB_CLASSES:
        try:
            job_instance = job_class()
            job_instance.register(scheduler)
            logger.info(f"Successfully registered job: {job_instance.job_id}")
        except Exception as e:
            logger.error(f"Failed to register job {job_class.__name__}: {e}")
            
    scheduler.start()
    logger.info("APScheduler started successfully.")
    return scheduler

