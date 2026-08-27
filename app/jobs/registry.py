from typing import Type
from app.jobs._base import BaseJob
from app.jobs.order_cleanup import OrderCleanupJob

# Explicitly register all jobs that are part of the scheduler.
# This makes the codebase highly readable and static-analysis friendly.
REGISTERED_JOB_CLASSES: list[Type[BaseJob]] = [
    OrderCleanupJob,
]

