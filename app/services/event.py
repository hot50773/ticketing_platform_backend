import logging
import traceback
from datetime import datetime, timezone, timedelta

from app.enums import SeatStatus, RedisSeatStatus

from app.cache.seat import SeatLockCache
from app.repositories.event import EventRepository
from app.repositories.order import OrderRepository

logger = logging.getLogger(__name__)


class EventService:
    ORDER_EXPIRATION_MINUTES = 10

    def __init__(
        self,
        event_repository: EventRepository,
        order_repository: OrderRepository,
        event_lock_cache: SeatLockCache,
    ):
        self._repository = event_repository
        self._order_repository = order_repository
        self.event_lock_cache = event_lock_cache


    async def list_seats(self, event_id: int) -> list[dict]:
        """Retrieves all seats for a given event, returning their ID, layout, and booking status."""
        db_seats = await self._repository.get_all_seats_by_event(event_id)
        if not db_seats:
            return []
        
        zone_groups = {}
        for detail in db_seats:
            zone_groups.setdefault(detail.event_seat.event_zone_id, []).append(detail)
            
        all_results = []
        for zone_id, items in zone_groups.items():
            size = len(items)
            seat_statuses = {}
            for detail in items:
                offset = detail.seat.serial_number - 1
                status_int = RedisSeatStatus.RELEASED.value
                es_status = detail.event_seat.status
                status_value = es_status.value if hasattr(es_status, "value") else es_status
                if status_value == SeatStatus.LOCK.value:
                    status_int = RedisSeatStatus.LOCKED.value
                elif status_value == SeatStatus.RESERVED.value:
                    status_int = RedisSeatStatus.RESERVED.value

                seat_statuses[offset] = status_int
                
            await self.event_lock_cache.init_zone_seats(zone_id, size, seat_statuses)
            
            offsets = [detail.seat.serial_number - 1 for detail in items]
            redis_statuses = await self.event_lock_cache.get_seats_status(zone_id, offsets)

            for idx, detail in enumerate(items):
                redis_val = redis_statuses[idx] if redis_statuses and idx < len(redis_statuses) else RedisSeatStatus.RELEASED.value
                if redis_val == RedisSeatStatus.LOCKED.value:
                    status = SeatStatus.LOCK.value
                elif redis_val == RedisSeatStatus.RESERVED.value:
                    status = SeatStatus.RESERVED.value
                else:
                    status = SeatStatus.RELEASED.value

                    
                all_results.append({
                    "id": detail.event_seat.id,
                    "seat_id": detail.seat.id,
                    "row": detail.seat.row,
                    "column": detail.seat.column,
                    "serial_number": detail.seat.serial_number,
                    "status": status,
                })
        return all_results


    async def lock_seats(self, event_id: int, seat_ids: list[int], user_id: int) -> bool:
        """Locks multiple seats in both Redis (for 10 min TTL) and PostgreSQL."""
        if not seat_ids:
            return False

        seat_details = await self._repository.get_event_seats_details(event_id, seat_ids)
        if len(seat_details) != len(seat_ids):
            return False

        zone_groups = {}
        for detail in seat_details:
            zone_groups.setdefault(detail.event_seat.event_zone_id, []).append(detail)

        locked_redis_zones = []
        
        payment_deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=self.ORDER_EXPIRATION_MINUTES)
        
        try:
            order_id = await self._order_repository.get_or_create_pending_order(user_id, event_id, payment_deadline)
        except Exception as e:
            logger.error(f"Failed to get or create pending order: {e}")
            return False

        try:
            for zone_id, items in zone_groups.items():
                all_zone_seats = await self._repository.get_all_seats_by_event(event_id)
                zone_seats = [x for x in all_zone_seats if x.event_seat.event_zone_id == zone_id]
                size = len(zone_seats)
                
                seat_statuses = {}
                for detail in zone_seats:
                    offset = detail.seat.serial_number - 1
                    status_int = RedisSeatStatus.RELEASED.value
                    es_status = detail.event_seat.status
                    status_value = es_status.value if hasattr(es_status, "value") else es_status
                    if status_value == SeatStatus.LOCK.value:
                        status_int = RedisSeatStatus.LOCKED.value
                    elif status_value == SeatStatus.RESERVED.value:
                        status_int = RedisSeatStatus.RESERVED.value
                    seat_statuses[offset] = status_int


                await self.event_lock_cache.init_zone_seats(zone_id, size, seat_statuses)
                
                offsets = [detail.seat.serial_number - 1 for detail in items]
                success = await self.event_lock_cache.lock_seats(zone_id, offsets, order_id)
                if not success:
                    raise ValueError("Seat already locked in Redis")
                    
                locked_redis_zones.append((zone_id, offsets))

            seat_prices = {detail.seat.id: detail.event_zone.price for detail in seat_details}
            await self._repository.execute_lock_seats_transaction(
                order_id, event_id, seat_ids, seat_prices
            )
            
            deadline_ts = payment_deadline.replace(tzinfo=timezone.utc).timestamp()
            await self.event_lock_cache.add_to_delay_queue(order_id, deadline_ts)
            return True
            
        except Exception as e:
            logger.warning(f"Failed to lock seats trace: {traceback.format_exc()}")
            for zone_id, offsets in locked_redis_zones:
                try:
                    await self.event_lock_cache.unlock_seats(zone_id, offsets, order_id)
                except Exception as redis_err:
                    logger.error(f"Failed to rollback Redis seats: {redis_err}")
            return False



    async def lock_seat(self, event_id: int, seat_id: int, user_id: int) -> bool:
        """Locks a seat in both Redis (for 10 min TTL) and PostgreSQL."""
        return await self.lock_seats(event_id, [seat_id], user_id)

    async def unlock_seats(self, event_id: int, seat_ids: list[int], user_id: int) -> bool:
        """Unlocks multiple seats, releasing the Redis lock and resetting PostgreSQL status."""
        if not seat_ids:
            return False

        seat_details = await self._repository.get_event_seats_details(event_id, seat_ids)
        if len(seat_details) != len(seat_ids):
            return False

        zone_groups = {}
        for detail in seat_details:
            zone_groups.setdefault(detail.event_seat.event_zone_id, []).append(detail)

        try:
            order_id, es_ids_to_release, remaining_tickets_count = await self._repository.execute_unlock_seats_transaction(
                event_id, seat_ids, user_id
            )
            
            for zone_id, items in zone_groups.items():
                offsets = [detail.seat.serial_number - 1 for detail in items]
                await self.event_lock_cache.unlock_seats(zone_id, offsets, order_id)
                
            if remaining_tickets_count <= 0:
                try:
                    await self.event_lock_cache.remove_from_delay_queue(order_id)
                except Exception:
                    pass
            return True
            
        except Exception as e:
            logger.error(f"Failed to unlock seats: {e}")
            return False


    async def unlock_seat(self, event_id: int, seat_id: int, user_id: int) -> bool:
        """Unlocks a seat, releasing the Redis lock and resetting PostgreSQL status."""
        return await self.unlock_seats(event_id, [seat_id], user_id)

    async def release_order_by_id(self, order_id: int) -> None:
        """Releases a single order by its ID, cancelling it and resetting seat statuses."""
    async def release_order_by_id(self, order_id: int) -> None:
        """Releases a single order by its ID, cancelling it and resetting seat statuses."""
        try:
            event_seat_ids = await self._repository.execute_release_order_transaction(order_id)
            
            if event_seat_ids:
                seats_details = await self._repository.get_seats_details_by_event_seat_ids(event_seat_ids)
                
                zone_groups = {}
                for detail in seats_details:
                    zone_groups.setdefault(detail.event_seat.event_zone_id, []).append(detail)
                    
                for zone_id, items in zone_groups.items():
                    offsets = [detail.seat.serial_number - 1 for detail in items]
                    await self.event_lock_cache.unlock_seats(zone_id, offsets, order_id)
                    
            try:
                await self.event_lock_cache.remove_from_delay_queue(order_id)
            except Exception:
                pass
            logger.info(f"Successfully released expired order {order_id} via ID release.")
        except Exception as e:
            logger.error(f"Error releasing order {order_id}: {e}")


    async def release_expired_orders(self) -> None:
        """Scan and release all pending orders that have exceeded the payment deadline using ZSET and DB fallback."""
        current_ts = datetime.now(timezone.utc).timestamp()
        
        # 1. High-performance ZSET check
        try:
            expired_order_ids = await self.event_lock_cache.get_expired_orders(current_ts)
            if expired_order_ids:
                logger.info(f"ZSET: Found {len(expired_order_ids)} expired orders to release: {expired_order_ids}")
                for oid in expired_order_ids:
                    await self.release_order_by_id(oid)
                    try:
                        await self.event_lock_cache.remove_from_delay_queue(oid)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error in ZSET release_expired_orders: {e}")

        # 2. Strong eventual consistency DB fallback
        try:
            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            expired_order_ids = await self._repository.get_expired_order_ids(now_naive)
                
            if expired_order_ids:
                logger.info(f"DB Fallback: Found {len(expired_order_ids)} expired orders to release.")
                for oid in expired_order_ids:
                    await self.release_order_by_id(oid)
                    try:
                        await self.event_lock_cache.remove_from_delay_queue(oid)
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Error in DB fallback release_expired_orders: {e}")


