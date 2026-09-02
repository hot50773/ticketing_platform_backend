from typing import Type
from app.jobs._base import BaseJob

# Explicitly register all jobs that are part of the scheduler.
# This makes the codebase highly readable and static-analysis friendly.
# Empty for now: the expired-order cleanup job comes back with the booking flow.
REGISTERED_JOB_CLASSES: list[Type[BaseJob]] = []
