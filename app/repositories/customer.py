import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.exceptions import EntityConflictError, EntityNotFoundError
from app.models import Customer
from app.schemas.customer import CustomerCreateInternal

logger = logging.getLogger(__name__)


class CustomerRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def create_customer(self, data: CustomerCreateInternal) -> Customer:
        async with self.session_factory() as session:
            try:
                customer = Customer(**data.model_dump())
                session.add(customer)
                await session.commit()
                await session.refresh(customer)
                return customer
            except IntegrityError as exc:
                await session.rollback()
                logger.error(exc)
                raise EntityConflictError(f"Customer '{data.email}' already exists") from exc

    async def get_customer(self, customer_id: int) -> Customer:
        async with self.session_factory() as session:
            customer = await session.get(Customer, customer_id)
        if customer is None:
            raise EntityNotFoundError(f"Customer with id {customer_id} not found")
        return customer

    async def get_by_email(self, email: str) -> Customer | None:
        async with self.session_factory() as session:
            return (await session.execute(select(Customer).where(Customer.email == email))).scalars().first()

    async def get_by_google_sub(self, google_sub: str) -> Customer | None:
        async with self.session_factory() as session:
            stmt = select(Customer).where(Customer.google_sub == google_sub)
            return (await session.execute(stmt)).scalars().first()

    async def link_google_sub(self, customer_id: int, google_sub: str) -> Customer:
        async with self.session_factory() as session:
            try:
                customer = await session.get_one(Customer, customer_id)
                customer.google_sub = google_sub
                await session.commit()
                await session.refresh(customer)
                return customer
            except IntegrityError as exc:
                await session.rollback()
                logger.error(exc)
                raise EntityConflictError(f"Google account '{google_sub}' is already linked") from exc
