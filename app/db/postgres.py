from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import settings

class Database:
    """Database connection wrapper exposing the engine and session maker."""

    def __init__(self):
        self._engine = create_async_engine(settings.database_url, echo=True)
        self.async_session_maker = async_sessionmaker(self._engine)

    def get_async_session(self) -> AsyncSession:
        """Returns a new database session."""
        return self.async_session_maker()

    @staticmethod
    async def close_session(session):
        """Closes a database session."""
        await session.close()

    async def close_all_sessions(self):
        """Closes all database sessions and disposes the engine."""
        await self._engine.dispose()
