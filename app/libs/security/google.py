import asyncio

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.exceptions import AuthenticationError
from app.schemas.auth import GoogleIdentityInternal


class GoogleIdTokenVerifier:
    """Checks an ID token the frontend got from Google Sign-In."""

    def __init__(self, client_id: str):
        self._client_id = client_id
        self._request = google_requests.Request()

    async def verify(self, token: str) -> GoogleIdentityInternal:
        if not self._client_id:
            raise AuthenticationError("Google sign-in is not configured")
        # verify_oauth2_token is blocking: it may fetch Google's signing certs
        try:
            claims = await asyncio.to_thread(
                id_token.verify_oauth2_token, token, self._request, self._client_id
            )
        except ValueError as exc:
            raise AuthenticationError("Invalid Google ID token") from exc

        if not claims.get("email_verified"):
            raise AuthenticationError("Google account email is not verified")
        try:
            return GoogleIdentityInternal(
                sub=claims["sub"], email=claims["email"], name=claims.get("name") or claims["email"]
            )
        except ValueError as exc:
            raise AuthenticationError("This Google account's email is not supported") from exc
