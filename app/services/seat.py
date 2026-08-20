import logging
from datetime import datetime, timezone, timedelta
from sqlmodel import select, delete
from app.cache.seat import SeatLockCache
from app.repositories.seat import SeatRepository
from app.enums import SeatStatus
from app.models._models import Cart, CartTicket, EventSeat, Seat

logger = logging.getLogger(__name__)

class SeatService:
    def __init__(self, seat_repository: SeatRepository, seat_lock_cache: SeatLockCache):
        self._repository = seat_repository
        self.seat_lock_cache = seat_lock_cache

    async def list_seats(self, event_id: int) -> list[dict]:
        """Retrieves all seats for a given event, returning their ID, layout, and booking status."""
        seats = await self._repository.get_all_seats_by_event(event_id)
        return [
            {
                "event_seat_id": event_seat[0].id,
                "seat_id": event_seat[1].id,
                "row": event_seat[1].row,
                "column": event_seat[1].column,
                "serial_number": event_seat[1].serial_number,
                "status": event_seat[0].status.value,
            }
            for event_seat in seats
        ]

    async def lock_seat(self, event_id: int, seat_id: int, user_id: int) -> bool:
        """Locks a seat in both Redis (for 15 min TTL) and PostgreSQL.
        
        Returns:
            True if the seat was successfully locked, False if it was already locked or sold.
        """
        # 1. Attempt to acquire the Redis distributed lock
        # 15 minutes = 900 seconds
        redis_locked = await self.seat_lock_cache.lock_seat(event_id, seat_id, user_id, ttl_seconds=900)
        if not redis_locked:
            return False

        # 2. Update PostgreSQL database status
        try:
            async with self._repository.session_factory() as session:
                # Retrieve the seat with a row-level lock (SELECT FOR UPDATE)
                db_seat = await self._repository.get_event_seat_with_lock(session, event_id, seat_id)
                
                if not db_seat:
                    logger.error(f"Seat {seat_id} for event {event_id} does not exist in DB.")
                    await self.seat_lock_cache.force_unlock_seat(event_id, seat_id)
                    return False

                if db_seat.status != SeatStatus.RELEASED:
                    logger.warning(f"Seat {seat_id} for event {event_id} is in status {db_seat.status.value}, cannot lock.")
                    await self.seat_lock_cache.force_unlock_seat(event_id, seat_id)
                    return False

                # Mark seat as LOCK (temporarily reserved)
                db_seat.status = SeatStatus.LOCK
                session.add(db_seat)

                # Find or create a Cart for this user and event
                cart_stmt = select(Cart).where(Cart.user_id == user_id, Cart.event_id == event_id)
                cart_result = await session.execute(cart_stmt)
                cart = cart_result.scalar_one_or_none()

                if not cart:
                    cart = Cart(
                        user_id=user_id,
                        event_id=event_id,
                        expired_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).replace(tzinfo=None)
                    )
                    session.add(cart)
                    await session.flush()  # Populates cart.id

                # Create CartTicket mapping
                cart_ticket = CartTicket(cart_id=cart.id, event_seat_id=db_seat.id)
                session.add(cart_ticket)

                await session.commit()
                logger.info(f"Successfully locked seat {seat_id} for event {event_id} and added to user {user_id}'s cart.")
                return True

        except Exception as e:
            logger.error(f"Error locking seat {seat_id} in DB: {str(e)}. Rolling back Redis lock.")
            await self.seat_lock_cache.force_unlock_seat(event_id, seat_id)
            raise e

    async def unlock_seat(self, event_id: int, seat_id: int, user_id: int) -> bool:
        """Unlocks a seat, releasing the Redis lock and resetting PostgreSQL status.
        
        Returns:
            True if the seat was successfully unlocked, False otherwise.
        """
        # 1. Release the Redis lock
        redis_unlocked = await self.seat_lock_cache.unlock_seat(event_id, seat_id, user_id)
        if not redis_unlocked:
            logger.warning(f"Failed to release Redis lock on seat {seat_id} for user {user_id}. The lock may have already expired.")
            # We still attempt to release it in the DB in case it got stuck in DB but expired in Redis.

        # 2. Update PostgreSQL database status
        try:
            async with self._repository.session_factory() as session:
                db_seat = await self._repository.get_event_seat_with_lock(session, event_id, seat_id)
                
                if db_seat and db_seat.status == SeatStatus.LOCK:
                    db_seat.status = SeatStatus.RELEASED
                    session.add(db_seat)

                    # Delete the CartTicket mapping
                    cart_stmt = select(Cart).where(Cart.user_id == user_id, Cart.event_id == event_id)
                    cart_result = await session.execute(cart_stmt)
                    cart = cart_result.scalar_one_or_none()
                    
                    if cart:
                        del_stmt = delete(CartTicket).where(
                            CartTicket.cart_id == cart.id,
                            CartTicket.event_seat_id == db_seat.id
                        )
                        await session.execute(del_stmt)

                    await session.commit()
                    logger.info(f"Successfully unlocked seat {seat_id} for event {event_id} in DB.")
                    return True
                else:
                    logger.warning(f"Seat {seat_id} for event {event_id} was not locked or doesn't exist in DB.")
                    return False
        except Exception as e:
            logger.error(f"Error unlocking seat {seat_id} in DB: {str(e)}.")
            raise e
