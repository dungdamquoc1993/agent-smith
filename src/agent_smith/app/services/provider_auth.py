"""Identity provider API-key and assertion authentication."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from agent_smith.app.auth import AppAssertionError, AppAssertionVerifier
from agent_smith.app.invocation import VerifiedActor
from agent_smith.app.ports.identity import (
    IdentityProviderAuthStore,
    IdentityProviderRecord,
    ProviderApiKeyRecord,
)

IDENTITY_SECRET_ENCRYPTION_SCHEME = "fernet:v1"
PROVIDER_API_KEY_PREFIX_LENGTH = 16


class IdentityProviderSecretCodec:
    def __init__(self, key: str | bytes) -> None:
        try:
            self._fernet = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise AppAssertionError(
                "invalid_identity_secrets_key",
                "identity_secrets_key is not a valid Fernet key.",
            ) from exc

    def encrypt(self, secret: str) -> str:
        return self._fernet.encrypt(secret.encode("utf-8")).decode("utf-8")

    def decrypt(self, encrypted: str) -> str:
        try:
            return self._fernet.decrypt(encrypted.encode("utf-8")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise AppAssertionError(
                "invalid_assertion_secret",
                "Unable to decrypt identity provider assertion secret.",
            ) from exc


class IdentityProviderAuthService:
    def __init__(
        self,
        store: IdentityProviderAuthStore,
        *,
        assertion_verifier: AppAssertionVerifier,
        secret_codec: IdentityProviderSecretCodec | None = None,
    ) -> None:
        self._store = store
        self._assertion_verifier = assertion_verifier
        self._secret_codec = secret_codec

    async def verify_actor(
        self,
        *,
        provider_api_key: str | None,
        authorization: str | None,
    ) -> VerifiedActor:
        provider, api_key = await self._resolve_provider_api_key(provider_api_key)
        assertion_keys = await self._active_assertion_keys(provider.id)
        actor = self._assertion_verifier.verify_for_provider(
            authorization,
            provider_id=provider.id,
            provider_slug=provider.slug,
            issuer=provider.issuer,
            keys=assertion_keys,
        )
        await self._store.mark_api_key_used(api_key.id, datetime.now(UTC))
        return actor

    async def verify_invocation(
        self,
        *,
        provider_api_key: str | None,
        authorization: str | None,
    ) -> VerifiedActor:
        """Backward-compatible alias for callers predating shared API authentication."""
        return await self.verify_actor(
            provider_api_key=provider_api_key,
            authorization=authorization,
        )

    async def _resolve_provider_api_key(
        self,
        provider_api_key: str | None,
    ) -> tuple[IdentityProviderRecord, ProviderApiKeyRecord]:
        provider_api_key = provider_api_key.strip() if provider_api_key else None
        if not provider_api_key:
            raise AppAssertionError("missing_provider_api_key", "Missing provider API key.")
        key_hash = hash_provider_api_key(provider_api_key)
        now = datetime.now(UTC)
        row = await self._store.find_provider_api_key(key_hash)
        if row is None:
            raise AppAssertionError("invalid_provider_api_key", "Invalid provider API key.")
        provider, api_key = row
        if provider.status != "active":
            raise AppAssertionError("provider_inactive", "Identity provider is not active.")
        if api_key.status != "active" or api_key.revoked_at is not None:
            raise AppAssertionError("provider_api_key_revoked", "Provider API key is not active.")
        if api_key.expires_at is not None and api_key.expires_at <= now:
            raise AppAssertionError("provider_api_key_expired", "Provider API key has expired.")
        return provider, api_key

    async def _active_assertion_keys(self, provider_id: str) -> dict[str, str]:
        if self._secret_codec is None:
            raise AppAssertionError(
                "identity_secrets_key_required",
                "identity_secrets_key is required for DB-backed identity provider assertions.",
            )
        now = datetime.now(UTC)
        rows = await self._store.list_assertion_keys(provider_id)
        keys: dict[str, str] = {}
        for row in rows:
            if row.expires_at is not None and row.expires_at <= now:
                continue
            if row.alg != "HS256":
                continue
            if row.encryption_scheme != IDENTITY_SECRET_ENCRYPTION_SCHEME:
                raise AppAssertionError(
                    "unsupported_assertion_secret_scheme",
                    f"Unsupported assertion secret encryption scheme: {row.encryption_scheme}",
                )
            keys[row.kid] = self._secret_codec.decrypt(row.encrypted_secret)
        if not keys:
            raise AppAssertionError(
                "missing_assertion_key", "No active assertion key for provider."
            )
        return keys


def generate_provider_api_key() -> str:
    return f"ask_{secrets.token_urlsafe(32)}"


def hash_provider_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def provider_api_key_prefix(raw_key: str) -> str:
    return raw_key[:PROVIDER_API_KEY_PREFIX_LENGTH]


def verify_provider_api_key(raw_key: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_provider_api_key(raw_key), expected_hash)


def generate_identity_secrets_key() -> str:
    return Fernet.generate_key().decode("utf-8")
