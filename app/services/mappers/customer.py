from app.models import Customer
from app.schemas.customer import CustomerResponse


class CustomerSchemaMapper:
    def to_customer(self, customer: Customer) -> CustomerResponse:
        return CustomerResponse.model_validate(customer, from_attributes=True)
