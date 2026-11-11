"""Who the caller is: manager password log-in, customer Google sign-in and token checks.

Customers and managers live in separate tables, so one email can be both.
Customers have no password: Google sign-in is their only way in.
"""
import asyncio
from typing import Callable, Protocol

from app.cache.login_attempts import LoginAttemptLimiter
from app.exceptions import (
    AuthenticationError,
    EntityConflictError,
    EntityNotFoundError,
    PermissionDeniedError,
    TooManyLoginAttemptsError,
)
from app.libs.security import AccessTokenCodec, InvalidTokenError, PasswordHasher, WrongAudienceError
from app.models import Customer
from app.repositories.customer import CustomerRepository
from app.repositories.manager import ManagerRepository
from app.schemas.auth import (
    CurrentCustomerInternal,
    CurrentManagerInternal,
    GoogleIdentityInternal,
    GoogleLogin,
    TokenResponse,
    normalize_email,
)
from app.schemas.customer import CustomerCreateInternal

INVALID_CREDENTIALS_MSG = "Incorrect email or password"
INVALID_TOKEN_MSG = "Invalid or expired token"


class GoogleVerifier(Protocol):
    async def verify(self, token: str) -> GoogleIdentityInternal:
        ...


class AuthService:
    def __init__(
        self,
        customer_repository: CustomerRepository,
        manager_repository: ManagerRepository,
        password_hasher: PasswordHasher,
        token_codec: AccessTokenCodec,
        google_verifier: GoogleVerifier,
        login_attempt_limiter: LoginAttemptLimiter,
    ):
        self._customers = customer_repository
        self._managers = manager_repository
        self._hasher = password_hasher
        self._codec = token_codec
        self._google_verifier = google_verifier
        self._limiter = login_attempt_limiter

    async def login_manager(self, email: str, password: str, client_ip: str) -> TokenResponse:
        """Unknown email, wrong password and a deactivated account give the same
        error and take the same time, so accounts cannot be probed for."""
        try:
            email = normalize_email(email)
        except ValueError as exc:
            raise AuthenticationError(INVALID_CREDENTIALS_MSG) from exc
        if await self._limiter.is_blocked(client_ip, email):
            raise TooManyLoginAttemptsError("Too many failed log-ins, try again later")

        manager = await self._managers.get_by_email(email)
        # argon2 is ~50ms of CPU; off the event loop so other requests keep moving
        valid, updated_hash = await asyncio.to_thread(
            self._hasher.verify_and_update, password, manager.hashed_password if manager else None
        )
        if not (valid and manager.is_active):
            await self._limiter.record_failure(client_ip, email)
            raise AuthenticationError(INVALID_CREDENTIALS_MSG)

        await self._limiter.reset(client_ip, email)
        if updated_hash:
            await self._managers.update_password(manager.id, updated_hash)
        return TokenResponse(access_token=self._codec.encode_manager(manager.id))

    async def login_with_google(self, payload: GoogleLogin) -> TokenResponse:
        identity = await self._google_verifier.verify(payload.id_token)
        customer = await self._customers.get_by_google_sub(identity.sub)
        if customer is None:
            customer = await self._first_google_sign_in(identity)
        return TokenResponse(access_token=self._codec.encode_customer(customer.id))

    async def _first_google_sign_in(self, identity: GoogleIdentityInternal) -> Customer:
        customer = await self._customers.get_by_email(identity.email)
        if customer is not None and customer.google_sub is not None:
            raise EntityConflictError(f"'{identity.email}' is linked to another Google account")

        try:
            customer = await self._link_or_register(customer, identity)
        except EntityConflictError:
            # A concurrent first sign-in with the same Google account got there first
            customer = await self._customers.get_by_google_sub(identity.sub)
            if customer is None:
                raise
        return customer

    async def _link_or_register(
        self, customer: Customer | None, identity: GoogleIdentityInternal
    ) -> Customer:
        if customer is None:
            customer = await self._customers.create_customer(
                CustomerCreateInternal(
                    name=identity.name, email=identity.email, google_sub=identity.sub
                )
            )
        else:
            # A customer from before Google sign-in. Google verified the email and
            # customers have no other way in, so this is the same person.
            customer = await self._customers.link_google_sub(customer.id, identity.sub)
        return customer

    def authenticate_customer(self, token: str) -> CurrentCustomerInternal:
        return CurrentCustomerInternal(id=self._read_token(self._codec.decode_customer, token))

    async def authenticate_manager(self, token: str) -> CurrentManagerInternal:
        """Checks the database on every request, so a deactivation or a role change
        takes effect before the token expires."""
        manager_id = self._read_token(self._codec.decode_manager, token)
        try:
            manager = await self._managers.get_manager(manager_id)
        except EntityNotFoundError as exc:
            raise AuthenticationError(INVALID_TOKEN_MSG) from exc
        if not manager.is_active:
            raise AuthenticationError("This account has been deactivated")
        return CurrentManagerInternal(id=manager.id, role=manager.role)

    @staticmethod
    def _read_token(decode: Callable[[str], int], token: str) -> int:
        try:
            return decode(token)
        except WrongAudienceError as exc:
            raise PermissionDeniedError("This account may not do this") from exc
        except InvalidTokenError as exc:
            raise AuthenticationError(INVALID_TOKEN_MSG) from exc
