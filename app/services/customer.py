from app.repositories.customer import CustomerRepository
from app.schemas.customer import CustomerResponse
from app.services.mappers.customer import CustomerSchemaMapper


class CustomerService:
    """Read-only: customers are created by Google sign-in in AuthService."""

    def __init__(
        self,
        customer_repository: CustomerRepository,
        customer_schema_mapper: CustomerSchemaMapper,
    ):
        self._repository = customer_repository
        self._mapper = customer_schema_mapper

    async def get_customer(self, customer_id: int) -> CustomerResponse:
        return self._mapper.to_customer(await self._repository.get_customer(customer_id))
