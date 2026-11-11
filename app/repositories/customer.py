import logging

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.exceptions import CUSTOMER_NOT_FOUND_MSG, EntityConflictError, EntityNotFoundError
from app.models import Customer
from app.schemas.customer import CustomerCreateInternal

logger = logging.getLogger(__name__)


class CustomerRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def get_or_create_by_google_sub(self, data: CustomerCreateInternal) -> Customer:
        """Two concurrent first sign-ins with one Google account both end up with the same row."""
        stmt = (
            insert(Customer)
            .values(**data.model_dump(), created_at=func.now(), updated_at=func.now())
            .on_conflict_do_nothing(index_elements=["google_sub"])
            .returning(Customer)
        )
        async with self.session_factory() as session:
            try:
                customer = (await session.scalars(stmt)).one_or_none()
                if customer is None:
                    customer = await session.scalar(
                        select(Customer).where(Customer.google_sub == data.google_sub)
                    )
                await session.commit()
                return customer
            except IntegrityError as exc:
                await session.rollback()
                logger.error(exc)
                raise EntityConflictError(f"Customer '{data.email}' already exists") from exc

    async def get_customer(self, customer_id: int) -> Customer:
        async with self.session_factory() as session:
            customer = await session.get(Customer, customer_id)
        if customer is None:
            raise EntityNotFoundError(CUSTOMER_NOT_FOUND_MSG.format(customer_id=customer_id))
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
