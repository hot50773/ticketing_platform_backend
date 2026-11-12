from httpx import AsyncClient

from app.enums import OrderStatus, PaymentStatus

API = "/api/v1"


async def test_paying_through_the_bound_gateway_confirms_the_order(
    anonymous_client: AsyncClient, customer_headers, seeded_event: dict, place_order
):
    customer = await customer_headers()
    customer_id = (await anonymous_client.get(f"{API}/auth/me", headers=customer)).json()["id"]
    order_id = await place_order(seeded_event["event_id"], customer_id)

    response = await anonymous_client.post(
        f"{API}/orders/{order_id}/pay", json={"payment_method": "card"}, headers=customer
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["payment_status"]) == (OrderStatus.CONFIRMED, PaymentStatus.SUCCESS)
