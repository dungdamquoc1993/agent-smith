"""Identity persistence contracts consumed by application services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

IdentityProviderStatus = Literal["active", "inactive", "pending"]
IdentityKeyStatus = Literal["active", "revoked", "expired"]


class IdentityStoreConflictError(Exception):
    """A backend uniqueness constraint rejected an identity write."""


@dataclass(frozen=True)
class IdentityPrincipal:
    id: str
    display_name: str


@dataclass(frozen=True)
class IdentityProviderRecord:
    id: str
    slug: str
    issuer: str
    display_name: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ProviderApiKeyRecord:
    id: str
    provider_id: str
    name: str
    key_hash: str
    key_prefix: str
    status: str
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ProviderAssertionKeyRecord:
    id: str
    provider_id: str
    kid: str
    alg: str
    encrypted_secret: str
    encryption_scheme: str
    status: str
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PrincipalIdentityStore(Protocol):
    async def consume_nonce(
        self,
        *,
        issuer: str,
        jti: str,
        expires_at: datetime,
    ) -> None: ...

    async def resolve_principal(
        self,
        *,
        provider_id: str,
        subject: str,
        email: str | None,
        display_name: str | None,
        metadata: dict[str, Any],
    ) -> IdentityPrincipal: ...


class IdentityProviderAuthStore(Protocol):
    async def find_provider_api_key(
        self,
        key_hash: str,
    ) -> tuple[IdentityProviderRecord, ProviderApiKeyRecord] | None: ...

    async def list_assertion_keys(
        self,
        provider_id: str,
    ) -> list[ProviderAssertionKeyRecord]: ...

    async def mark_api_key_used(self, api_key_id: str, used_at: datetime) -> None: ...


class IdentityProviderAdminStore(Protocol):
    async def create_provider(
        self,
        *,
        slug: str,
        issuer: str,
        display_name: str,
        status: str,
        metadata: dict[str, Any],
    ) -> IdentityProviderRecord: ...

    async def list_providers(self) -> list[IdentityProviderRecord]: ...

    async def get_provider(self, provider_id: str) -> IdentityProviderRecord | None: ...

    async def update_provider(
        self,
        provider_id: str,
        changes: dict[str, Any],
    ) -> IdentityProviderRecord | None: ...

    async def create_api_key(
        self,
        *,
        provider_id: str,
        name: str,
        key_hash: str,
        key_prefix: str,
        expires_at: datetime | None,
    ) -> ProviderApiKeyRecord | None: ...

    async def list_api_keys(self, provider_id: str) -> list[ProviderApiKeyRecord] | None: ...

    async def revoke_api_key(
        self,
        key_id: str,
        revoked_at: datetime,
    ) -> ProviderApiKeyRecord | None: ...

    async def create_assertion_key(
        self,
        *,
        provider_id: str,
        kid: str,
        alg: str,
        encrypted_secret: str,
        encryption_scheme: str,
        expires_at: datetime | None,
    ) -> ProviderAssertionKeyRecord | None: ...

    async def list_provider_assertion_keys(
        self,
        provider_id: str,
    ) -> list[ProviderAssertionKeyRecord] | None: ...

    async def revoke_assertion_key(
        self,
        key_id: str,
        revoked_at: datetime,
    ) -> ProviderAssertionKeyRecord | None: ...
