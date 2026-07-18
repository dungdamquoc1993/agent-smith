"""Production password, token, and time primitives for admin authentication."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError


class Argon2AdminPasswordHasher:
    """Argon2id password hashing with parameters centralized for future rehashing."""

    def __init__(
        self,
        *,
        time_cost: int = 3,
        memory_cost: int = 65_536,
        parallelism: int = 4,
        hash_len: int = 32,
        salt_len: int = 16,
    ) -> None:
        self._hasher = PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=hash_len,
            salt_len=salt_len,
            type=Type.ID,
        )

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except (InvalidHashError, TypeError, ValueError):
            return True


class UrlSafeTokenGenerator:
    def generate(self) -> str:
        return secrets.token_urlsafe(32)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
