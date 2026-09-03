"""Proof that check-then-write cannot be raced.

Wrapping a use case in one transaction is not enough on its own: under READ
COMMITTED a concurrent transaction can commit between our count and our update,
and we would never see it. What closes the gap is the `FOR UPDATE` that
EventRepository.load_for_update takes *before* reading any state.

These tests simulate the contract the booking flow will have to honour: take
`FOR SHARE` on the event row before creating an order. Shared locks do not
block each other, so ticket grabbing stays concurrent, while an admin's
`FOR UPDATE` waits for the in-flight orders and blocks new ones.

Every test cleans up its session and its pending task in a finally / teardown:
a leaked row lock would hang the next test's TRUNCATE rather than fail it.
"""
import asyncio

import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.containers import Container
from app.enums import OrderStatus, PaymentStatus
from app.models import Event, Order

API = "/api/v1/events"
SETTLE = 0.3  # long enough for the admin request to reach the lock and queue


@pytest.fixture
async def in_flight_booking(container: Container, seeded_event: dict):
    """A purchase mid-transaction: holds FOR SHARE on the event row and has
    inserted an order, but has not committed."""
    session = container.db().get_async_session()
    await session.__aenter__()
    await session.execute(
        select(Event)
        .where(Event.id == seeded_event["event_id"])
        .with_for_update(read=True)
    )
    session.add(
        Order(
            user_id=seeded_event["user_id"],
            event_id=seeded_event["event_id"],
            checkout_amount=1500,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
    )
    await session.flush()

    yield session

    try:
        await session.rollback()
    finally:
        await session.__aexit__(None, None, None)


async def assert_admin_call_waits_then_conflicts(admin_call, booking):
    """The admin request must queue behind the booking, then see its order."""
    task = asyncio.create_task(admin_call)
    try:
        await asyncio.sleep(SETTLE)
        assert not task.done(), (
            "the admin request did not wait for the event row - it read the "
            "order count before taking the lock"
        )
        await booking.commit()
        resp = await asyncio.wait_for(task, timeout=5.0)
        assert resp.status_code == 409, resp.text
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_delete_cannot_miss_an_order_committed_mid_flight(
    async_client: AsyncClient, seeded_event: dict, in_flight_booking
):
    """The race this whole layer exists to prevent: without the lock the delete
    counts zero orders, the booking commits, and an event somebody has bought a
    seat for is destroyed."""
    event_id = seeded_event["event_id"]
    await assert_admin_call_waits_then_conflicts(
        async_client.delete(f"{API}/{event_id}"), in_flight_booking
    )
    assert (await async_client.get(f"{API}/{event_id}")).status_code == 200


@pytest.mark.asyncio
async def test_unpublish_cannot_miss_an_order_committed_mid_flight(
    async_client: AsyncClient, seeded_event: dict, in_flight_booking
):
    await assert_admin_call_waits_then_conflicts(
        async_client.patch(f"{API}/{seeded_event['event_id']}/status", json={"is_active": False}),
        in_flight_booking,
    )


@pytest.mark.asyncio
async def test_reads_never_queue_behind_a_writer(
    async_client: AsyncClient, container: Container, seeded_event: dict
):
    """load() deliberately takes no lock: a browsing customer must not wait
    behind an admin edit."""
    session = container.db().get_async_session()
    await session.__aenter__()
    try:
        await session.execute(
            select(Event).where(Event.id == seeded_event["event_id"]).with_for_update()
        )
        resp = await asyncio.wait_for(
            async_client.get(f"{API}/{seeded_event['event_id']}"), timeout=2.0
        )
        assert resp.status_code == 200
    finally:
        await session.rollback()
        await session.__aexit__(None, None, None)
