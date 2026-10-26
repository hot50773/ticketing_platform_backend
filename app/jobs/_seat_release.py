from typing import TYPE_CHECKING

from dependency_injector.wiring import inject

from ._base import BaseJob

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from app.services.seat_release import SeatReleaseService


# class ProcessExpiredOrder(BaseJob):
#     job_id='process_expired_order'
#     trigger_type="cron"
#     trigger_args={"minute":'*/15'}
#
#     @inject
#     def __init__(self, seat_release_service: "SeatReleaseService"):
#         self._service = seat_release_service
#
#     async def run(self) -> None:
#         await self._service.release_unclaimed_seats()
#
#
#     def register(self, scheduler: "AsyncIOScheduler") -> None:
#         scheduler.add_job(
#             self.run,
#             self.trigger_type,
#             id=self.job_id,
#             replace_existing=True,
#             **self.trigger_args,
#         )
