from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError


class PasswordHasher:
    def __init__(self):
        self._hash = PasswordHash.recommended()
        # Verified against when the account is missing, so both cases cost one argon2 run
        self._dummy_hash = self._hash.hash("dummy-password-for-constant-time")

    def hash(self, password: str) -> str:
        return self._hash.hash(password)

    def verify(self, password: str, hashed_password: str | None) -> bool:
        return self.verify_and_update(password, hashed_password)[0]

    def verify_and_update(self, password: str, hashed_password: str | None) -> tuple[bool, str | None]:
        """Returns (valid, new hash when the stored one uses outdated parameters)."""
        valid, updated_hash = False, None
        try:
            if hashed_password:
                valid, updated_hash = self._hash.verify_and_update(password, hashed_password)
            else:
                self._hash.verify(password, self._dummy_hash)
        except UnknownHashError:
            pass
        return valid, updated_hash
