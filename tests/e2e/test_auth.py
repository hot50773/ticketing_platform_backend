"""Accounts and permissions end to end: Google sign-in, manager log-in, admin, route guards."""
from datetime import timedelta

from httpx import AsyncClient

from sqlmodel import select

from app.containers import Container
from app.enums import ManagerRole
from app.libs.clock.functional import now_utc
from app.models import Order
from app.schemas.auth import GoogleIdentityInternal
from app.schemas.manager import ManagerCreate
from app.scripts.create_admin import create_admin
from tests.unit._fakes import FakeGoogleIdTokenVerifier

API = "/api/v1"
MANAGER_LOGIN = "admin/auth/login"


async def login(client: AsyncClient, email: str, password: str, path: str = MANAGER_LOGIN):
    return await client.post(f"{API}/{path}", data={"username": email, "password": password})


async def bearer(client: AsyncClient, email: str, password: str, path: str = MANAGER_LOGIN) -> dict[str, str]:
    response = await login(client, email, password, path)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def an_event_payload(venue_id: int) -> dict:
    start_at = now_utc() + timedelta(days=30)
    return {
        "name": "Show", "description": "d", "venue_id": venue_id,
        "start_at": start_at.isoformat(), "end_at": (start_at + timedelta(hours=2)).isoformat(),
    }


# --------------------------------------------------------------------------- #
# Customer Google sign-in
# --------------------------------------------------------------------------- #
async def google_sign_in(
    client: AsyncClient, container: Container, email: str = "gina@example.com", sub: str = "g-1"
):
    identity = GoogleIdentityInternal(sub=sub, email=email, name="Gina")
    with container.google_id_token_verifier.override(FakeGoogleIdTokenVerifier(identity)):
        return await client.post(f"{API}/auth/google", json={"id_token": "t"})


async def test_customers_have_no_password_routes(anonymous_client: AsyncClient):
    body = {"name": "Carol", "email": "carol@example.com", "password": "correct-horse"}

    assert (await anonymous_client.post(f"{API}/auth/register", json=body)).status_code in (404, 405)
    assert (await login(anonymous_client, "carol@example.com", "correct-horse", "auth/login")).status_code in (404, 405)


async def test_google_sign_in_registers_a_customer(
    anonymous_client: AsyncClient, container: Container
):
    response = await google_sign_in(anonymous_client, container)

    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    blocked = await anonymous_client.post(f"{API}/admin/venues", json={}, headers=headers)
    assert blocked.status_code == 403


# --------------------------------------------------------------------------- #
# Admin and managers
# --------------------------------------------------------------------------- #
async def test_admin_from_the_cli_adds_a_manager_who_can_then_edit_events(
    anonymous_client: AsyncClient, container: Container, seeded_venue_and_seats: dict
):
    await create_admin(
        container, ManagerCreate(name="Root", email="root@example.com", password="admin-password")
    )
    admin = await bearer(anonymous_client, "root@example.com", "admin-password", MANAGER_LOGIN)

    created = await anonymous_client.post(
        f"{API}/admin/managers",
        json={"name": "Mia", "email": "mia@example.com", "password": "manager-password"},
        headers=admin,
    )
    assert created.status_code == 201
    assert created.json()["role"] == ManagerRole.MANAGER

    manager = await bearer(anonymous_client, "mia@example.com", "manager-password", MANAGER_LOGIN)
    response = await anonymous_client.post(
        f"{API}/admin/events", json=an_event_payload(seeded_venue_and_seats["venue_id"]), headers=manager
    )
    assert response.status_code == 201


async def test_only_admin_can_add_managers_or_list_managers(
    anonymous_client: AsyncClient, customer_headers, manager_headers
):
    payload = {"name": "Mia", "email": "mia@example.com", "password": "manager-password"}
    for headers in [await customer_headers(), await manager_headers()]:
        assert (await anonymous_client.post(f"{API}/admin/managers", json=payload, headers=headers)).status_code == 403
        assert (await anonymous_client.get(f"{API}/admin/managers", headers=headers)).status_code == 403

    admin = await manager_headers(ManagerRole.ADMIN)
    response = await anonymous_client.get(f"{API}/admin/managers?role=MANAGER", headers=admin)
    assert response.status_code == 200
    assert {manager["role"] for manager in response.json()["items"]} == {ManagerRole.MANAGER}


async def test_admin_can_edit_events_too(
    anonymous_client: AsyncClient, manager_headers, seeded_venue_and_seats: dict
):
    response = await anonymous_client.post(
        f"{API}/admin/events",
        json=an_event_payload(seeded_venue_and_seats["venue_id"]),
        headers=await manager_headers(ManagerRole.ADMIN),
    )

    assert response.status_code == 201


# --------------------------------------------------------------------------- #
# Route guards
# --------------------------------------------------------------------------- #
async def test_admin_routes_need_a_manager(
    anonymous_client: AsyncClient, customer_headers, seeded_event: dict
):
    event_id = seeded_event["event_id"]
    customer = await customer_headers()
    writes = [
        ("post", f"{API}/admin/venues", {}),
        ("get", f"{API}/admin/events", None),
        ("post", f"{API}/admin/events", {}),
        ("patch", f"{API}/admin/events/{event_id}/profile", {"name": "x", "description": "d"}),
        ("post", f"{API}/admin/events/{event_id}/publish", None),
        ("delete", f"{API}/admin/events/{event_id}", None),
        ("delete", f"{API}/admin/orders/1", {"reason": "x"}),
        ("get", f"{API}/admin/auth/me", None),
    ]
    for method, url, body in writes:
        kwargs = {} if body is None else {"json": body}

        anonymous = await anonymous_client.request(method, url, **kwargs)
        as_customer = await anonymous_client.request(method, url, headers=customer, **kwargs)

        assert (anonymous.status_code, as_customer.status_code) == (401, 403), url


async def test_drafts_are_hidden_from_the_public_but_not_from_managers(
    anonymous_client: AsyncClient, manager_headers, seeded_event: dict
):
    event_id, zone_id = seeded_event["event_id"], seeded_event["zone_ids"]["ZONE_A"]
    detail, seats = f"events/{event_id}", f"events/{event_id}/event_zones/{zone_id}/seats"
    manager = await manager_headers()

    assert (await anonymous_client.get(f"{API}/events/selling")).status_code == 200
    assert (await anonymous_client.get(f"{API}/{detail}")).status_code == 404
    assert (await anonymous_client.get(f"{API}/{seats}")).status_code == 404
    assert (await anonymous_client.get(f"{API}/admin/{detail}", headers=manager)).status_code == 200
    assert (await anonymous_client.get(f"{API}/admin/{seats}", headers=manager)).status_code == 200


async def test_manager_routes_are_gone_from_the_public_paths(
    anonymous_client: AsyncClient, manager_headers, seeded_event: dict
):
    manager = await manager_headers()
    old_paths = [f"{API}/events", f"{API}/venues", f"{API}/auth/token", f"{API}/admin/auth/token"]

    for url in old_paths:
        response = await anonymous_client.post(url, json={}, headers=manager)
        assert response.status_code in (404, 405), url


async def test_orders_need_a_customer(anonymous_client: AsyncClient, manager_headers):
    body = {"event_id": 1, "event_seat_ids": [1]}
    manager = await manager_headers()

    assert (await anonymous_client.post(f"{API}/orders", json=body)).status_code == 401
    assert (await anonymous_client.post(f"{API}/orders", json=body, headers=manager)).status_code == 403
    assert (await anonymous_client.get(f"{API}/orders", headers=manager)).status_code == 403


async def test_customers_see_only_their_own_orders(
    anonymous_client: AsyncClient, customer_headers, seeded_event: dict, place_order
):
    alice = await customer_headers(email="alice-c@example.com")
    bob = await customer_headers(email="bob-c@example.com")
    alice_id = (await anonymous_client.get(f"{API}/auth/me", headers=alice)).json()["id"]
    order_id = await place_order(seeded_event["event_id"], alice_id)

    assert (await anonymous_client.get(f"{API}/orders/{order_id}", headers=alice)).status_code == 200
    assert (await anonymous_client.get(f"{API}/orders/{order_id}", headers=bob)).status_code == 404
    assert (await anonymous_client.post(f"{API}/orders/{order_id}/cancel", headers=bob)).status_code == 404

    alice_orders = (await anonymous_client.get(f"{API}/orders", headers=alice)).json()["items"]
    bob_orders = (await anonymous_client.get(f"{API}/orders", headers=bob)).json()["items"]
    assert [order["id"] for order in alice_orders] == [order_id]
    assert bob_orders == []


async def test_each_me_route_answers_only_its_own_kind_of_account(
    anonymous_client: AsyncClient, customer_headers, manager_headers
):
    manager = await manager_headers(email="mia@example.com")
    customer = await customer_headers(email="carol@example.com")

    response = await anonymous_client.get(f"{API}/admin/auth/me", headers=manager)
    assert response.status_code == 200
    assert (response.json()["email"], response.json()["role"]) == ("mia@example.com", ManagerRole.MANAGER)
    assert (await anonymous_client.get(f"{API}/auth/me", headers=customer)).json()["email"] == "carol@example.com"

    assert (await anonymous_client.get(f"{API}/auth/me", headers=manager)).status_code == 403
    assert (await anonymous_client.get(f"{API}/admin/auth/me", headers=customer)).status_code == 403


async def test_a_customer_and_a_manager_may_share_an_email(
    anonymous_client: AsyncClient, container: Container, manager_headers
):
    """The tables are separate, so these are two accounts that only look alike."""
    await manager_headers(email="mia@example.com")

    response = await google_sign_in(anonymous_client, container, email="mia@example.com")
    customer = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = (await anonymous_client.get(f"{API}/auth/me", headers=customer)).json()
    assert me["email"] == "mia@example.com" and "role" not in me


async def test_email_case_neither_opens_a_second_account_nor_breaks_log_in(
    anonymous_client: AsyncClient, manager_headers
):
    admin = await manager_headers(ManagerRole.ADMIN)
    body = {"name": "Mia", "password": "manager-password"}

    response = await anonymous_client.post(f"{API}/admin/managers", json=body | {"email": "Mia@Example.COM"}, headers=admin)
    assert response.json()["email"] == "mia@example.com"
    response = await anonymous_client.post(f"{API}/admin/managers", json=body | {"email": "mia@example.com"}, headers=admin)
    assert response.status_code == 409
    await bearer(anonymous_client, "MIA@example.com", "manager-password")


async def test_a_non_ascii_email_is_rejected_not_reported_as_taken(
    anonymous_client: AsyncClient, manager_headers
):
    admin = await manager_headers(ManagerRole.ADMIN)
    body = {"name": "José", "email": "josé@example.com", "password": "manager-password"}

    assert (await anonymous_client.post(f"{API}/admin/managers", json=body, headers=admin)).status_code == 422


async def test_wrong_password_is_401_with_a_bearer_challenge(anonymous_client: AsyncClient, container: Container):
    await create_admin(
        container, ManagerCreate(name="Root", email="root@example.com", password="admin-password")
    )

    response = await login(anonymous_client, "root@example.com", "wrong-password")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_repeated_failed_log_ins_are_throttled(anonymous_client: AsyncClient, container: Container):
    await create_admin(
        container, ManagerCreate(name="Root", email="root@example.com", password="admin-password")
    )

    for _ in range(5):
        assert (await login(anonymous_client, "root@example.com", "wrong-password")).status_code == 401

    assert (await login(anonymous_client, "root@example.com", "admin-password")).status_code == 429


# --------------------------------------------------------------------------- #
# Deactivation and audit
# --------------------------------------------------------------------------- #
async def test_a_deactivated_manager_is_locked_out_before_the_token_expires(
    anonymous_client: AsyncClient, container: Container, manager_headers
):
    admin = await manager_headers(ManagerRole.ADMIN)
    created = await anonymous_client.post(
        f"{API}/admin/managers",
        json={"name": "Mia", "email": "mia@example.com", "password": "manager-password"},
        headers=admin,
    )
    manager_id = created.json()["id"]
    manager = await bearer(anonymous_client, "mia@example.com", "manager-password", MANAGER_LOGIN)

    response = await anonymous_client.post(f"{API}/admin/managers/{manager_id}/deactivate", headers=admin)
    assert (response.status_code, response.json()["is_active"]) == (200, False)
    assert (await anonymous_client.get(f"{API}/admin/events", headers=manager)).status_code == 401
    assert (await login(anonymous_client, "mia@example.com", "manager-password", MANAGER_LOGIN)).status_code == 401

    await anonymous_client.post(f"{API}/admin/managers/{manager_id}/activate", headers=admin)
    assert (await anonymous_client.get(f"{API}/admin/events", headers=manager)).status_code == 200


async def test_only_admins_deactivate_and_only_managers_can_be_deactivated(
    anonymous_client: AsyncClient, manager_headers
):
    admin = await manager_headers(ManagerRole.ADMIN, email="root@example.com")
    other_admin = await manager_headers(ManagerRole.ADMIN, email="root2@example.com")
    manager = await manager_headers()
    other_admin_id = (await anonymous_client.get(f"{API}/admin/auth/me", headers=other_admin)).json()["id"]
    manager_id = (await anonymous_client.get(f"{API}/admin/auth/me", headers=manager)).json()["id"]

    response = await anonymous_client.post(f"{API}/admin/managers/{manager_id}/deactivate", headers=manager)
    assert response.status_code == 403
    response = await anonymous_client.post(f"{API}/admin/managers/{other_admin_id}/deactivate", headers=admin)
    assert response.status_code == 403
    response = await anonymous_client.post(f"{API}/admin/managers/9999/deactivate", headers=admin)
    assert response.status_code == 404


async def test_deleting_an_order_records_which_manager_did_it(
    anonymous_client: AsyncClient, container: Container, manager_headers, seeded_event: dict, place_order
):
    manager = await manager_headers()
    manager_id = (await anonymous_client.get(f"{API}/admin/auth/me", headers=manager)).json()["id"]
    order_id = await place_order(seeded_event["event_id"], seeded_event["customer_id"])
    async with container.db().get_async_session() as session:
        order = await session.get(Order, order_id)
        order.payment_deadline = now_utc() - timedelta(minutes=1)
        await session.commit()

    response = await anonymous_client.request(
        "delete", f"{API}/admin/orders/{order_id}", json={"reason": "duplicate"}, headers=manager
    )

    assert response.status_code == 204
    async with container.db().get_async_session() as session:
        order = await session.scalar(select(Order).where(Order.id == order_id))
    assert (order.deleted_by_manager_id, order.comment) == (manager_id, "duplicate")


async def test_manager_writes_are_audited_with_their_outcome(
    anonymous_client: AsyncClient, manager_headers, caplog
):
    manager = await manager_headers()

    with caplog.at_level("INFO", logger="app.audit"):
        await anonymous_client.get(f"{API}/admin/events", headers=manager)
        await anonymous_client.post(f"{API}/admin/events/9999/publish", headers=manager)

    audit = [record.getMessage() for record in caplog.records if record.name == "app.audit"]
    assert len(audit) == 1
    assert audit[0].endswith("POST /api/v1/admin/events/9999/publish -> 404")
