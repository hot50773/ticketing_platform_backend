from httpx import AsyncClient
from sqlmodel import select

from app.containers import Container
from app.enums import OrderStatus
from app.libs.clock.functional import now_utc
from app.models import EventSeat, EventZone, Order, OrderTicket

API = "/api/v1"


async def manager_id_of(client: AsyncClient, headers: dict[str, str]) -> int:
    return (await client.get(f"{API}/admin/auth/me", headers=headers)).json()["id"]


async def test_managers_list_every_customers_orders_with_filters(
    anonymous_client: AsyncClient, manager_headers, customer_headers, seeded_event: dict, place_order
):
    event_id = seeded_event["event_id"]
    alice, bob = seeded_event["customer_id"], seeded_event["customer_id"] + 1
    await place_order(event_id, alice)
    await place_order(event_id, bob)
    cancelled = await place_order(event_id, bob, status=OrderStatus.CANCELLED)
    deleted = await place_order(event_id, alice, soft_deleted=True)
    manager = await manager_headers()

    async def ids(query: str = "") -> list[int]:
        response = await anonymous_client.get(f"{API}/admin/orders{query}", headers=manager)
        assert response.status_code == 200
        return [order["id"] for order in response.json()["items"]]

    assert len(await ids()) == 3
    assert deleted not in await ids()
    assert deleted in await ids("?include_deleted=true")
    assert len(await ids(f"?customer_id={bob}")) == 2
    assert await ids("?status=CANCELLED") == [cancelled]
    assert await ids(f"?event_id={event_id + 1}") == []

    customer = await customer_headers()
    assert (await anonymous_client.get(f"{API}/admin/orders", headers=customer)).status_code == 403


async def test_a_manager_sees_a_deleted_order_with_the_tickets_it_held(
    anonymous_client: AsyncClient, container: Container, manager_headers, seeded_event: dict
):
    manager = await manager_headers()
    manager_id = await manager_id_of(anonymous_client, manager)
    async with container.db().get_async_session() as session:
        seat = await session.scalar(
            select(EventSeat)
            .join(EventZone, EventZone.id == EventSeat.event_zone_id)
            .where(EventZone.event_id == seeded_event["event_id"])
            .order_by(EventSeat.id)
        )
        now = now_utc()
        order = Order(
            customer_id=seeded_event["customer_id"], event_id=seeded_event["event_id"],
            checkout_amount=1500, payment_deadline=now, comment="duplicate",
            deleted_at=now, deleted_by_manager_id=manager_id,
        )
        session.add(order)
        await session.flush()
        session.add(OrderTicket(order_id=order.id, event_seat_id=seat.id, amount=1500, deleted_at=now))
        await session.commit()
        order_id, seat_id = order.id, seat.id

    response = await anonymous_client.get(f"{API}/admin/orders/{order_id}", headers=manager)

    assert response.status_code == 200
    body = response.json()
    assert body["deleted_at"] is not None
    assert (body["deleted_by_manager_id"], body["comment"]) == (manager_id, "duplicate")
    assert [ticket["event_seat_id"] for ticket in body["tickets"]] == [seat_id]
    assert (await anonymous_client.get(f"{API}/admin/orders/9999", headers=manager)).status_code == 404
