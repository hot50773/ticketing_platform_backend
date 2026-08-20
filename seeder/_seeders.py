from abc import ABC, abstractmethod
from sqlalchemy import delete
from app.db.postgres import Database
from app.models import User

class DataSeeder(ABC):
    """Abstract base class for seeding target destinations."""
    @abstractmethod
    async def clear(self) -> None:
        """Clear existing data to ensure seeding is idempotent."""
        pass

    @abstractmethod
    async def seed(self, data: list[str]) -> None:
        """Seed the target destination with data."""
        pass

class PostgresSeeder(DataSeeder):
    """Seeds records into the PostgreSQL database using the Database connection wrapper."""
    def __init__(self, batch_size: int = 1000):
        self.batch_size = batch_size
        # Instantiate the class-based Database connection wrapper
        self.db = Database()

    async def clear(self) -> None:
        async with self.db.get_async_session() as session:
            await session.execute(delete(User))
            await session.commit()

    async def seed(self, data: list[str]) -> None:
        total_records = len(data)
        async with self.db.get_async_session() as session:
            for i in range(0, total_records, self.batch_size):
                batch_names = data[i : i + self.batch_size]
                nickname_objs = [User(name=name) for name in batch_names]
                session.add_all(nickname_objs)
                await session.commit()
