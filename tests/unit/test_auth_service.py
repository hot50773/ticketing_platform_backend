"""AuthService: customer and manager log-in, Google sign-in and token checks, without a database."""
from datetime import timedelta

import jwt
import pytest
from pwdlib.hashers.argon2 import Argon2Hasher

from app.enums import ManagerRole
from app.exceptions import (
    AuthenticationError,
    EntityConflictError,
    PermissionDeniedError,
    TooManyLoginAttemptsError,
)
from app.libs.clock.functional import now_utc
from app.libs.security import AccessTokenCodec, GoogleIdTokenVerifier, PasswordHasher
from app.schemas.auth import GoogleIdentityInternal, GoogleLogin
from app.services.auth import AuthService
from tests.unit._fakes import (
    FakeCustomerRepository,
    FakeGoogleIdTokenVerifier,
    FakeLoginAttemptLimiter,
    FakeManagerRepository,
)

SECRET = "test-secret-at-least-32-bytes-long!!"
IP = "203.0.113.7"


def a_codec() -> AccessTokenCodec:
    return AccessTokenCodec(secret_key=SECRET, expire_minutes=5)


def a_service(
    customers: FakeCustomerRepository | None = None,
    managers: FakeManagerRepository | None = None,
    verifier: FakeGoogleIdTokenVerifier | None = None,
    limiter: FakeLoginAttemptLimiter | None = None,
) -> AuthService:
    return AuthService(
        customer_repository=customers or FakeCustomerRepository(),
        manager_repository=managers or FakeManagerRepository(),
        password_hasher=PasswordHasher(),
        token_codec=a_codec(),
        google_verifier=verifier or FakeGoogleIdTokenVerifier(),
        login_attempt_limiter=limiter or FakeLoginAttemptLimiter(),
    )


def a_google_identity(email: str = "carol@example.com", sub: str = "g-123"):
    return GoogleIdentityInternal(sub=sub, email=email, name="Carol")


def hashed(password: str) -> str:
    return PasswordHasher().hash(password)


def a_manager(managers: FakeManagerRepository, role: ManagerRole = ManagerRole.MANAGER, **fields):
    return managers.add(
        **{"name": "Mia", "email": "mia@example.com", "hashed_password": hashed("pw-12345678"), "role": role}
        | fields
    )


# --------------------------------------------------------------------------- #
# Manager password log-in
# --------------------------------------------------------------------------- #
async def test_manager_log_in_returns_a_manager_token():
    managers = FakeManagerRepository()
    manager = a_manager(managers)

    token = await a_service(managers=managers).login_manager("mia@example.com", "pw-12345678", IP)

    assert a_codec().decode_manager(token.access_token) == manager.id
    assert token.token_type == "bearer"


async def test_wrong_password_unknown_email_and_deactivated_account_fail_the_same_way():
    managers = FakeManagerRepository()
    a_manager(managers)
    a_manager(managers, email="gone@example.com", is_active=False)
    service = a_service(managers=managers)
    errors = []

    for email, password in [
        ("mia@example.com", "wrong"),
        ("nobody@example.com", "pw-12345678"),
        ("gone@example.com", "pw-12345678"),
    ]:
        with pytest.raises(AuthenticationError) as error:
            await service.login_manager(email, password, IP)
        errors.append(str(error.value))

    assert len(set(errors)) == 1


async def test_a_stored_value_that_is_not_a_hash_fails_like_a_wrong_password():
    managers = FakeManagerRepository()
    a_manager(managers, hashed_password="1")

    with pytest.raises(AuthenticationError):
        await a_service(managers=managers).login_manager("mia@example.com", "1", IP)


async def test_repeated_failures_block_even_the_right_password():
    managers = FakeManagerRepository()
    a_manager(managers)
    service = a_service(managers=managers, limiter=FakeLoginAttemptLimiter(max_failures=2))

    for _ in range(2):
        with pytest.raises(AuthenticationError):
            await service.login_manager("mia@example.com", "wrong", IP)

    with pytest.raises(TooManyLoginAttemptsError):
        await service.login_manager("mia@example.com", "pw-12345678", IP)
    await service.login_manager("mia@example.com", "pw-12345678", "198.51.100.1")


async def test_a_successful_log_in_clears_the_failure_count():
    managers = FakeManagerRepository()
    a_manager(managers)
    limiter = FakeLoginAttemptLimiter(max_failures=2)
    service = a_service(managers=managers, limiter=limiter)

    with pytest.raises(AuthenticationError):
        await service.login_manager("mia@example.com", "wrong", IP)
    await service.login_manager("mia@example.com", "pw-12345678", IP)

    assert limiter.failures == {}


async def test_a_clean_log_in_skips_the_reset_round_trip():
    managers = FakeManagerRepository()
    a_manager(managers)
    limiter = FakeLoginAttemptLimiter()

    await a_service(managers=managers, limiter=limiter).login_manager("mia@example.com", "pw-12345678", IP)

    assert limiter.resets == 0


async def test_a_deactivated_manager_with_the_right_password_still_counts_as_a_failure():
    managers = FakeManagerRepository()
    a_manager(managers, is_active=False)
    limiter = FakeLoginAttemptLimiter()

    with pytest.raises(AuthenticationError):
        await a_service(managers=managers, limiter=limiter).login_manager("mia@example.com", "pw-12345678", IP)

    assert limiter.failures == {(IP, "mia@example.com"): 1}


async def test_a_hash_with_outdated_parameters_is_upgraded_at_log_in():
    managers = FakeManagerRepository()
    weak = Argon2Hasher(time_cost=1, memory_cost=8192).hash("pw-12345678")
    manager = a_manager(managers, hashed_password=weak)

    await a_service(managers=managers).login_manager("mia@example.com", "pw-12345678", IP)

    assert manager.hashed_password != weak
    assert PasswordHasher().verify_and_update("pw-12345678", manager.hashed_password)[0]


# --------------------------------------------------------------------------- #
# Google sign-in
# --------------------------------------------------------------------------- #
async def test_first_google_sign_in_registers_a_customer():
    customers = FakeCustomerRepository()
    verifier = FakeGoogleIdTokenVerifier(a_google_identity())

    token = await a_service(customers=customers, verifier=verifier).login_with_google(GoogleLogin(id_token="t"))

    customer = await customers.get_by_google_sub("g-123")
    assert customer.email == "carol@example.com"
    assert a_codec().decode_customer(token.access_token) == customer.id


async def test_returning_google_customer_gets_the_same_account():
    customers = FakeCustomerRepository()
    service = a_service(customers=customers, verifier=FakeGoogleIdTokenVerifier(a_google_identity()))

    first = await service.login_with_google(GoogleLogin(id_token="t"))
    second = await service.login_with_google(GoogleLogin(id_token="t"))

    assert a_codec().decode_customer(first.access_token) == a_codec().decode_customer(second.access_token)
    assert len(customers.rows) == 1


async def test_a_customer_from_before_google_sign_in_is_linked_by_email():
    customers = FakeCustomerRepository()
    existing = customers.add(name="Carol", email="carol@example.com")
    service = a_service(customers=customers, verifier=FakeGoogleIdTokenVerifier(a_google_identity()))

    token = await service.login_with_google(GoogleLogin(id_token="t"))

    assert a_codec().decode_customer(token.access_token) == existing.id
    assert existing.google_sub == "g-123"
    assert len(customers.rows) == 1


async def test_google_sign_in_with_a_manager_email_opens_a_separate_customer():
    customers = FakeCustomerRepository()
    managers = FakeManagerRepository()
    a_manager(managers)
    verifier = FakeGoogleIdTokenVerifier(a_google_identity(email="mia@example.com"))

    token = await a_service(customers, managers, verifier).login_with_google(GoogleLogin(id_token="t"))

    with pytest.raises(PermissionDeniedError):
        await a_service(customers, managers).authenticate_manager(token.access_token)
    assert len(customers.rows) == 1


async def test_email_already_linked_to_another_google_account_conflicts():
    customers = FakeCustomerRepository()
    customers.add(name="Carol", email="carol@example.com", google_sub="g-other")
    verifier = FakeGoogleIdTokenVerifier(a_google_identity())

    with pytest.raises(EntityConflictError):
        await a_service(customers=customers, verifier=verifier).login_with_google(GoogleLogin(id_token="t"))


async def test_google_verifier_rejects_an_email_that_cannot_be_stored(monkeypatch):
    from google.oauth2 import id_token

    claims = {"sub": "g-1", "email": "josé@example.com", "email_verified": True}
    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda *args: claims)

    with pytest.raises(AuthenticationError):
        await GoogleIdTokenVerifier(client_id="client").verify("t")

    claims["email"] = "Gina@Example.COM"
    assert (await GoogleIdTokenVerifier(client_id="client").verify("t")).email == "gina@example.com"


async def test_rejected_google_token_creates_nothing():
    customers = FakeCustomerRepository()
    verifier = FakeGoogleIdTokenVerifier(error=AuthenticationError("bad token"))

    with pytest.raises(AuthenticationError):
        await a_service(customers=customers, verifier=verifier).login_with_google(GoogleLogin(id_token="t"))

    assert customers.rows == {}


# --------------------------------------------------------------------------- #
# Token checks
# --------------------------------------------------------------------------- #
async def test_authenticate_rejects_a_tampered_or_expired_token():
    managers = FakeManagerRepository()
    a_manager(managers, role=ManagerRole.ADMIN)
    service = a_service(managers=managers)
    expired = AccessTokenCodec(secret_key=SECRET, expire_minutes=-1).encode_manager(1)
    forged = AccessTokenCodec(secret_key="x" * 32, expire_minutes=5).encode_manager(1)

    for token in [expired, forged, "not-a-jwt"]:
        with pytest.raises(AuthenticationError):
            await service.authenticate_manager(token)
        with pytest.raises(AuthenticationError):
            service.authenticate_customer(token)


async def test_a_token_without_an_audience_is_rejected():
    """The shape tokens had before customers and managers were told apart."""
    managers = FakeManagerRepository()
    a_manager(managers, role=ManagerRole.ADMIN)
    claims = {"sub": "1", "role": "ADMIN", "iat": now_utc(), "exp": now_utc() + timedelta(minutes=5)}
    token = jwt.encode(claims, SECRET, algorithm="HS256")

    with pytest.raises(AuthenticationError):
        await a_service(managers=managers).authenticate_manager(token)


async def test_a_customer_token_cannot_pass_as_the_manager_with_the_same_id():
    managers = FakeManagerRepository()
    a_manager(managers, role=ManagerRole.ADMIN)
    service = a_service(managers=managers)

    with pytest.raises(PermissionDeniedError):
        await service.authenticate_manager(a_codec().encode_customer(1))
    with pytest.raises(PermissionDeniedError):
        service.authenticate_customer(a_codec().encode_manager(1))


async def test_authenticate_manager_reads_the_role_from_the_database():
    """A role change applies to tokens already issued."""
    managers = FakeManagerRepository()
    manager = a_manager(managers)
    service = a_service(managers=managers)
    token = a_codec().encode_manager(manager.id)

    manager.role = ManagerRole.ADMIN

    assert (await service.authenticate_manager(token)).role == ManagerRole.ADMIN
    assert service.authenticate_customer(a_codec().encode_customer(7)).id == 7


async def test_a_deactivated_or_deleted_manager_loses_access_before_the_token_expires():
    managers = FakeManagerRepository()
    manager = a_manager(managers)
    service = a_service(managers=managers)
    token = a_codec().encode_manager(manager.id)

    manager.is_active = False
    with pytest.raises(AuthenticationError):
        await service.authenticate_manager(token)

    del managers.rows[manager.id]
    with pytest.raises(AuthenticationError):
        await service.authenticate_manager(token)
