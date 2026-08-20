from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models import User

class SeatRepository:
    """Repository class managing nickname database interactions."""
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def add(self, nickname: User) -> User:
        """Saves a new nickname object to the PostgreSQL database."""
        async with self.session_factory() as session:
            session.add(nickname)
            await session.commit()
            await session.refresh(nickname)
            return nickname

    async def get_all_names(self) -> list[str]:
        """Fetches all nickname names from the database."""
        async with self.session_factory() as session:
            result = await session.execute(select(User.name))
            return list(result.scalars().all())
