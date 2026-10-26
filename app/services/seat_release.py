"""The background sweep that hands seats back.

Releasing spans Postgres and Redis with no transaction over both, and the
moment it matters most - a ten-minute hold during a rush - is also the moment
nobody is watching. So every step here is built to be re-run: the sweep makes
progress, crashes, and the next round picks up where it stopped.

Two phases, one path. Phase 1 soft-deletes orders whose hold ran out; Phase 2
hands back the seats of every order that is already gone, whoever deleted it.
A user deleting an order does not release its seats itself - it only soft
-deletes, and Phase 2 finds it like any other.

TODO:
 需要釋放的訂單狀態：CANCELED / PENDING && now > payment_deadline && order deleted
"""
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from app.cache.event_layout import EventLayoutCache
from app.cache.job_lock import JobLock
from app.exceptions import SeatNotReleasableError
from app.libs.clock.functional import now_utc
from app.repositories.event import EventRepository
from app.repositories.order import OrderRepository
from app.schemas.event import SeatPositionInternal
from app.schemas.order import OrderDelete, OrderSeatInternal
from app.services.validators.order import OrderValidator

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 50
EXPIRED_REASON = "付款期限已過，系統自動釋放"


class SeatReleaseService:
    """Hands back seats that no live order is holding."""

    def __init__(
        self,
        order_repository: OrderRepository,
        event_repository: EventRepository,
        event_layout_cache: EventLayoutCache,
        order_validator: OrderValidator,
        job_lock: JobLock,
        batch_size: int = DEFAULT_BATCH_SIZE,
        clock: Callable[[], datetime] = now_utc,
    ):
        self._orders = order_repository
        self._events = event_repository
        self._cache = event_layout_cache
        self._validator = order_validator
        self._lock = job_lock
        self._batch_size = batch_size
        self._now = clock

    async def run(self) -> None:
        """One sweep, if nobody else already holds the lock: two runners on the
        same batch could clear a seat a customer has meanwhile legitimately taken.
        """
        async with self._lock.held() as executable:
            if not executable:
                logger.info("Another sweep holds the lock; skipping this round")
                return

            expired = await self.expire_overdue_orders()
            released = await self.release_unclaimed_seats()
            if expired or released:
                logger.info("Sweep expired %s orders and released %s", expired, released)

    # ----------------------------------------------------------------------- #
    # Phase 1
    # ----------------------------------------------------------------------- #
    async def process_expired_order(self) -> int:
        """Soft-deletes orders whose hold has run out. Seats are Phase 2's job.

        Re-validates under the row lock: the query ran without one, so the
        customer may have paid in the meantime.
        """
        order_ids = await self._orders.get_expired_order_ids(
            now=self._now(), limit=self._batch_size
        )
        expired = 0
        for order_id in order_ids:
            try:
                async with self._orders.load_for_update(order_id) as order:
                    self._validator.check_deletable(order.state)
                    await order.soft_delete_cascade(OrderDelete(reason=EXPIRED_REASON))
                expired += 1
            except Exception:
                logger.exception("Could not expire order %s", order_id)
        return expired

    # ----------------------------------------------------------------------- #
    # Phase 2
    # ----------------------------------------------------------------------- #
    async def release_unclaimed_seats(self) -> int:
        """Hands back the seats of orders that are already gone.

        Candidates come from two places because neither sees the whole picture:
        the database finds work nobody has started, and the pending flags find
        work that started and did not finish - by which point the database row
        it would have been found by is already released.
        """
        discovered = await self._orders.get_unreleased_seats(self._batch_size)
        pending = await self._cache.pending_releases()

        order_ids = {seat.order_id for seat in discovered}
        order_ids |= {flag.order_id for flag in pending}
        seats = await self._orders.get_seats_for_orders(sorted(order_ids))

        released = 0
        for event_id, by_order in self._group_by_event(seats).items():
            released += await self._release_one_event(event_id, by_order)

        await self._drop_flags_without_seats(pending, {seat.order_id for seat in seats})
        return released

    async def _release_one_event(
        self, event_id: int, by_order: dict[int, list[OrderSeatInternal]]
    ) -> int:
        """One flag per order, then one UPDATE for the whole event, then one
        Lua call per order to clear its bits and its flag together."""
        try:
            for order_id in by_order:
                await self._cache.mark_release_pending(event_id, order_id)

            async with self._events.load_for_seat_release(event_id) as event:
                await event.check_and_release_seats(
                    [seat.event_seat_id for seats in by_order.values() for seat in seats]
                )

            for order_id, seats in by_order.items():
                await self._cache.release_seats(
                    event_id=event_id,
                    seats=[
                        SeatPositionInternal(
                            event_zone_id=seat.event_zone_id,
                            serial_number=seat.serial_number,
                        )
                        for seat in seats
                    ],
                    clear_pending=order_id,
                )
            return len(by_order)
        except SeatNotReleasableError:
            logger.warning(
                "Event %s: seats of orders %s are gone, dropping them",
                event_id, sorted(by_order),
            )
            await self._drop_flags(event_id, by_order)
            return 0
        except Exception:
            logger.exception("Event %s: release failed, retrying next sweep", event_id)
            return 0

    async def _drop_flags_without_seats(self, pending, order_ids_with_seats) -> None:
        """A flag whose order never committed has nothing to release, but the
        flag itself still has to go or the sweep carries it for ever."""
        for flag in pending:
            if flag.order_id not in order_ids_with_seats:
                await self._cache.release_seats(
                    event_id=flag.event_id, seats=[], clear_pending=flag.order_id
                )

    async def _drop_flags(self, event_id: int, by_order) -> None:
        for order_id in by_order:
            await self._cache.release_seats(
                event_id=event_id, seats=[], clear_pending=order_id
            )

    @staticmethod
    def _group_by_event(
        seats: list[OrderSeatInternal],
    ) -> dict[int, dict[int, list[OrderSeatInternal]]]:
        """event -> order -> seats. The event is the unit of work: one row lock
        and one UPDATE for all of its orders rather than one per order."""
        grouped: dict[int, dict[int, list[OrderSeatInternal]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for seat in seats:
            grouped[seat.event_id][seat.order_id].append(seat)
        return grouped
