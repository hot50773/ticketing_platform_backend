from datetime import timedelta
from typing import Any

import jwt

from app.libs.clock.functional import now_utc

ALGORITHM = "HS256"
# Both tables count ids from 1, so `aud` is what keeps customer 1 from passing as manager 1.
CUSTOMER_AUDIENCE = "customer"
MANAGER_AUDIENCE = "manager"


class InvalidTokenError(Exception):
    """Malformed, forged or expired."""


class WrongAudienceError(InvalidTokenError):
    """A valid token issued to the other kind of account."""


class AccessTokenCodec:
    """Signs and reads the account id. Whether the account may still act is the caller's call."""

    def __init__(self, secret_key: str, expire_minutes: int):
        self._secret_key = secret_key
        self._expire = timedelta(minutes=expire_minutes)

    def encode_customer(self, customer_id: int) -> str:
        return self._encode(customer_id, CUSTOMER_AUDIENCE)

    def encode_manager(self, manager_id: int) -> str:
        return self._encode(manager_id, MANAGER_AUDIENCE)

    def decode_customer(self, token: str) -> int:
        return int(self._decode(token, CUSTOMER_AUDIENCE)["sub"])

    def decode_manager(self, token: str) -> int:
        return int(self._decode(token, MANAGER_AUDIENCE)["sub"])

    def _encode(self, account_id: int, audience: str) -> str:
        now = now_utc()
        claims = {"sub": str(account_id), "aud": audience, "iat": now, "exp": now + self._expire}
        return jwt.encode(claims, self._secret_key, algorithm=ALGORITHM)

    def _decode(self, token: str, audience: str) -> dict[str, Any]:
        try:
            return jwt.decode(
                token,
                self._secret_key,
                algorithms=[ALGORITHM],
                audience=audience,
                options={"require": ["exp", "iat", "sub", "aud"]},
            )
        except jwt.InvalidAudienceError as exc:
            raise WrongAudienceError from exc
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError from exc
