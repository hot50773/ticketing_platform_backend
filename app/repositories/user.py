import logging
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.exceptions import (
    EntityNotFoundError,
    EntityConflictError,
    ActiveEventsExistError,
    VENUE_NOT_FOUND_MSG,
    ZONE_NOT_FOUND_MSG,
    SEAT_NOT_FOUND_MSG,
)
from app.models import Venue, Zone, Seat, Event, User
from app.repositories._pagination import paginate
from app.schemas import Page, PaginationParams
from app.schemas.user import UserCreate
from app.schemas.venue import VenueCreate, ZoneBase, SeatCreate
from app.libs.clock.functional import now_utc

logger = logging.getLogger(__name__)

class UserRepository:
    """Repository class managing database transactions and persistence for Venue, Zone, and Seat."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def create_user(self, user_data: UserCreate) -> User:
        """Persists a new Venue record in the database."""
        async with self.session_factory() as session:
            try:
                user = User(
                    name=user_data.name,
                    email=user_data.email,
                    hashed_password="1"
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
                return user
            except IntegrityError as exc:
                await session.rollback()
                logger.error(exc)
                raise EntityConflictError(
                    f"User account:'{user_data.email}' already exists"
                ) from exc

    async def get_users(self, pagination: PaginationParams) -> Page[User]:
        """Retrieves one page of non-deleted venues ordered by ID."""
        async with self.session_factory() as session:
            stmt = select(User).where(User.deleted_at == None).order_by(User.id)
            return await paginate(session, stmt, pagination, User)
