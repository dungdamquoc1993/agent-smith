"""Identity capability-private row mappers."""

from typing import Any

from agent_smith.app.ports.identity import (
    IdentityProviderRecord,
    ProviderApiKeyRecord,
    ProviderAssertionKeyRecord,
)
from agent_smith.infra.storage.postgres.models.identity_providers import (
    IdentityProvider,
    IdentityProviderApiKey,
    IdentityProviderAssertionKey,
)


def provider_record(row: IdentityProvider) -> IdentityProviderRecord:
    return IdentityProviderRecord(
        id=str(row.id),
        slug=row.slug,
        issuer=row.issuer,
        display_name=row.display_name,
        status=_enum_value(row.status),
        metadata=dict(row.provider_metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def api_key_record(row: IdentityProviderApiKey) -> ProviderApiKeyRecord:
    return ProviderApiKeyRecord(
        id=str(row.id),
        provider_id=str(row.provider_id),
        name=row.name,
        key_hash=row.key_hash,
        key_prefix=row.key_prefix,
        status=_enum_value(row.status),
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def assertion_key_record(row: IdentityProviderAssertionKey) -> ProviderAssertionKeyRecord:
    return ProviderAssertionKeyRecord(
        id=str(row.id),
        provider_id=str(row.provider_id),
        kid=row.kid,
        alg=row.alg,
        encrypted_secret=row.encrypted_secret,
        encryption_scheme=row.encryption_scheme,
        status=_enum_value(row.status),
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)
