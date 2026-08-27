from abc import ABC, abstractmethod
from apscheduler.schedulers.asyncio import AsyncIOScheduler

class BaseJob(ABC):
    """Base interface for all scheduled jobs in the application."""
    
    @property
    @abstractmethod
    def job_id(self) -> str:
        """Unique identifier for the job."""
        pass

    @property
    @abstractmethod
    def trigger_type(self) -> str:
        """Trigger type, e.g., 'interval' or 'cron'."""
        pass

    @property
    @abstractmethod
    def trigger_args(self) -> dict:
        """Arguments for the trigger (e.g., {'minutes': 1})."""
        pass

    @abstractmethod
    async def run(self) -> None:
        """The actual business logic execution."""
        pass

    def register(self, scheduler: AsyncIOScheduler) -> None:
        """Helper to register this job instance to the scheduler."""
        scheduler.add_job(
            self.run,
            self.trigger_type,
            id=self.job_id,
            replace_existing=True,
            **self.trigger_args
        )
