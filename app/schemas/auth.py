from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator
from sqlmodel import SQLModel

from app.enums import ManagerRole


def normalize_email(value: str) -> str:
    """ASCII-only, lower-cased. Python and Postgres lower-case non-ASCII text
    differently, which would trip the email = lower(email) CHECK."""
    try:
        ascii_email = validate_email(value, check_deliverability=False).ascii_email
    except EmailNotValidError as exc:
        raise ValueError(str(exc)) from exc
    if ascii_email is None:
        raise ValueError("Only ASCII characters are allowed before the @")
    return ascii_email.lower()


# The domain is kept in its punycode form (xn--...)
Email = Annotated[str, AfterValidator(normalize_email)]


class GoogleLogin(SQLModel):
    """POST /auth/google: the ID token the frontend got from Google Sign-In."""
    id_token: str


class TokenResponse(SQLModel):
    access_token: str
    token_type: str = "bearer"


# --------------------------------------------------------------------------- #
# Internal transport
# --------------------------------------------------------------------------- #
class CurrentCustomerInternal(SQLModel):
    id: int


class CurrentManagerInternal(SQLModel):
    id: int
    role: ManagerRole


class GoogleIdentityInternal(SQLModel):
    sub: str
    email: Email
    name: str

