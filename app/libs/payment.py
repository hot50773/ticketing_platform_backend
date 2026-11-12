import uuid
from typing import Protocol


class PaymentGateway(Protocol):
    async def charge(self, order_id: int, amount: int, payment_method: str) -> str:
        """Returns the provider's transaction id.

        Raising must mean no money moved: the caller marks the payment FAILED and
        lets the customer pay again. A timeout or other unknown outcome has to be
        settled with the provider first, or a retry charges the customer twice.
        """
        ...


class UnimplementedPaymentGateway:
    """The production default, so no order is confirmed without real money behind it."""

    async def charge(self, order_id: int, amount: int, payment_method: str) -> str:
        raise NotImplementedError(
            "No payment provider is wired in. Set PAYMENT_GATEWAY or bind a real "
            "PaymentGateway in app/containers.py before enabling POST /orders/{order_id}/pay."
        )


class FakePaymentGateway:
    """Always succeeds. Development and tests only."""

    async def charge(self, order_id: int, amount: int, payment_method: str) -> str:
        return f"fake-{order_id}-{uuid.uuid4().hex}"


PAYMENT_GATEWAYS: dict[str, type[PaymentGateway]] = {
    "unimplemented": UnimplementedPaymentGateway,
    "fake": FakePaymentGateway,
}
