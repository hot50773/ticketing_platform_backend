import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.exceptions import EntityConflictError, EntityNotFoundError
from app.models import Manager
from app.repositories._pagination import paginate
from app.schemas import Page
from app.schemas.manager import ManagerCreateInternal, ManagerListParams

logger = logging.getLogger(__name__)


class ManagerRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def create_manager(self, data: ManagerCreateInternal) -> Manager:
        async with self.session_factory() as session:
            try:
                manager = Manager(**data.model_dump())
                session.add(manager)
                await session.commit()
                await session.refresh(manager)
                return manager
            except IntegrityError as exc:
                await session.rollback()
                logger.error(exc)
                raise EntityConflictError(f"Manager '{data.email}' already exists") from exc

    async def get_manager(self, manager_id: int) -> Manager:
        async with self.session_factory() as session:
            manager = await session.get(Manager, manager_id)
        if manager is None:
            raise EntityNotFoundError(f"Manager with id {manager_id} not found")
        return manager

    async def get_by_email(self, email: str) -> Manager | None:
        async with self.session_factory() as session:
            return (await session.execute(select(Manager).where(Manager.email == email))).scalars().first()

    async def update_password(self, manager_id: int, hashed_password: str) -> None:
        async with self.session_factory() as session:
            manager = await session.get_one(Manager, manager_id)
            manager.hashed_password = hashed_password
            await session.commit()

    async def set_active(self, manager_id: int, is_active: bool) -> Manager:
        async with self.session_factory() as session:
            manager = await session.get(Manager, manager_id)
            if manager is None:
                raise EntityNotFoundError(f"Manager with id {manager_id} not found")
            manager.is_active = is_active
            await session.commit()
            await session.refresh(manager)
            return manager

    async def get_manager_list(self, params: ManagerListParams) -> Page[Manager]:
        async with self.session_factory() as session:
            stmt = select(Manager).order_by(Manager.id)
            if params.role is not None:
                stmt = stmt.where(Manager.role == params.role)
            return await paginate(session, stmt, params, Manager)
