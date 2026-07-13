"""Identity provider management use-cases."""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from agent_smith.app.ports.identity import (
    IdentityProviderAdminStore,
    IdentityProviderRecord,
    IdentityProviderStatus,
    IdentityStoreConflictError,
    ProviderApiKeyRecord,
    ProviderAssertionKeyRecord,
)
from agent_smith.app.services.provider_auth import (
    IDENTITY_SECRET_ENCRYPTION_SCHEME,
    IdentityProviderSecretCodec,
    generate_provider_api_key,
    hash_provider_api_key,
    provider_api_key_prefix,
)

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,126}[a-z0-9]$")


class IdentityProviderManagementError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class CreateIdentityProviderRequest(BaseModel):
    slug: str = Field(min_length=2, max_length=128)
    issuer: str = Field(min_length=2, max_length=128)
    display_name: str = Field(alias="displayName", min_length=1, max_length=255)
    status: IdentityProviderStatus = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        value = value.strip()
        if not SLUG_PATTERN.fullmatch(value):
            raise ValueError("slug must use lowercase letters, numbers, underscores, or dashes")
        return value

    @field_validator("issuer", "display_name")
    @classmethod
    def strip_required_string(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class UpdateIdentityProviderRequest(BaseModel):
    slug: str | None = Field(default=None, min_length=2, max_length=128)
    issuer: str | None = Field(default=None, min_length=2, max_length=128)
    display_name: str | None = Field(
        default=None, alias="displayName", min_length=1, max_length=255
    )
    status: IdentityProviderStatus | None = None
    metadata: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not SLUG_PATTERN.fullmatch(value):
            raise ValueError("slug must use lowercase letters, numbers, underscores, or dashes")
        return value

    @field_validator("issuer", "display_name")
    @classmethod
    def strip_optional_string(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class CreateProviderApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    model_config = {"populate_by_name": True}

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class CreateAssertionKeyRequest(BaseModel):
    kid: str = Field(min_length=1, max_length=128)
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    model_config = {"populate_by_name": True}

    @field_validator("kid")
    @classmethod
    def strip_kid(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("kid must not be blank")
        return value


class IdentityProviderManagementService:
    def __init__(
        self,
        store: IdentityProviderAdminStore,
        *,
        secret_codec: IdentityProviderSecretCodec | None = None,
    ) -> None:
        self._store = store
        self._secret_codec = secret_codec

    async def create_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _validate(CreateIdentityProviderRequest, payload)
        try:
            provider = await self._store.create_provider(
                slug=request.slug,
                issuer=request.issuer,
                display_name=request.display_name,
                status=request.status,
                metadata=request.metadata,
            )
        except IdentityStoreConflictError as exc:
            raise _conflict(
                "identity_provider_conflict",
                "Provider slug or issuer already exists.",
            ) from exc
        return {"identityProvider": identity_provider_payload(provider)}

    async def list_providers(self) -> dict[str, Any]:
        rows = await self._store.list_providers()
        return {"identityProviders": [identity_provider_payload(row) for row in rows]}

    async def get_provider(self, provider_id: str) -> dict[str, Any]:
        provider = await self._require_provider(provider_id)
        return {"identityProvider": identity_provider_payload(provider)}

    async def update_provider(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = _validate(UpdateIdentityProviderRequest, payload)
        resolved_id = _validated_id(provider_id, "invalid_provider_id", "Invalid provider id.")
        changes = {
            field: getattr(request, field)
            for field in ("slug", "issuer", "display_name", "status", "metadata")
            if getattr(request, field) is not None
        }
        try:
            provider = await self._store.update_provider(resolved_id, changes)
        except IdentityStoreConflictError as exc:
            raise _conflict(
                "identity_provider_conflict",
                "Provider slug or issuer already exists.",
            ) from exc
        if provider is None:
            raise _not_found("provider_not_found", "Identity provider was not found.")
        return {"identityProvider": identity_provider_payload(provider)}

    async def create_api_key(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = _validate(CreateProviderApiKeyRequest, payload)
        resolved_id = _validated_id(provider_id, "invalid_provider_id", "Invalid provider id.")
        raw_key = generate_provider_api_key()
        api_key = await self._store.create_api_key(
            provider_id=resolved_id,
            name=request.name,
            key_hash=hash_provider_api_key(raw_key),
            key_prefix=provider_api_key_prefix(raw_key),
            expires_at=request.expires_at,
        )
        if api_key is None:
            raise _not_found("provider_not_found", "Identity provider was not found.")
        data = api_key_payload(api_key)
        data["rawKey"] = raw_key
        return {"apiKey": data}

    async def list_api_keys(self, provider_id: str) -> dict[str, Any]:
        resolved_id = _validated_id(provider_id, "invalid_provider_id", "Invalid provider id.")
        rows = await self._store.list_api_keys(resolved_id)
        if rows is None:
            raise _not_found("provider_not_found", "Identity provider was not found.")
        return {"apiKeys": [api_key_payload(row) for row in rows]}

    async def revoke_api_key(self, key_id: str) -> dict[str, Any]:
        resolved_id = _validated_id(key_id, "invalid_api_key_id", "Invalid API key id.")
        api_key = await self._store.revoke_api_key(resolved_id, datetime.now(UTC))
        if api_key is None:
            raise _not_found("api_key_not_found", "Provider API key was not found.")
        return {"apiKey": api_key_payload(api_key)}

    async def create_assertion_key(
        self, provider_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if self._secret_codec is None:
            raise IdentityProviderManagementError(
                "identity_secrets_key_required",
                "AGENT_SMITH_IDENTITY_SECRETS_KEY is required to create assertion keys.",
                status=500,
            )
        request = _validate(CreateAssertionKeyRequest, payload)
        resolved_id = _validated_id(provider_id, "invalid_provider_id", "Invalid provider id.")
        raw_secret = secrets.token_urlsafe(48)
        try:
            assertion_key = await self._store.create_assertion_key(
                provider_id=resolved_id,
                kid=request.kid,
                alg="HS256",
                encrypted_secret=self._secret_codec.encrypt(raw_secret),
                encryption_scheme=IDENTITY_SECRET_ENCRYPTION_SCHEME,
                expires_at=request.expires_at,
            )
        except IdentityStoreConflictError as exc:
            raise _conflict(
                "assertion_key_conflict",
                "Assertion key kid already exists for provider.",
            ) from exc
        if assertion_key is None:
            raise _not_found("provider_not_found", "Identity provider was not found.")
        data = assertion_key_payload(assertion_key)
        data["rawSecret"] = raw_secret
        return {"assertionKey": data}

    async def list_assertion_keys(self, provider_id: str) -> dict[str, Any]:
        resolved_id = _validated_id(provider_id, "invalid_provider_id", "Invalid provider id.")
        rows = await self._store.list_provider_assertion_keys(resolved_id)
        if rows is None:
            raise _not_found("provider_not_found", "Identity provider was not found.")
        return {"assertionKeys": [assertion_key_payload(row) for row in rows]}

    async def revoke_assertion_key(self, key_id: str) -> dict[str, Any]:
        resolved_id = _validated_id(key_id, "invalid_assertion_key_id", "Invalid assertion key id.")
        assertion_key = await self._store.revoke_assertion_key(resolved_id, datetime.now(UTC))
        if assertion_key is None:
            raise _not_found("assertion_key_not_found", "Assertion key was not found.")
        return {"assertionKey": assertion_key_payload(assertion_key)}

    async def _require_provider(self, provider_id: str) -> IdentityProviderRecord:
        resolved_id = _validated_id(provider_id, "invalid_provider_id", "Invalid provider id.")
        provider = await self._store.get_provider(resolved_id)
        if provider is None:
            raise _not_found("provider_not_found", "Identity provider was not found.")
        return provider


def identity_provider_payload(provider: IdentityProviderRecord) -> dict[str, Any]:
    return {
        "id": provider.id,
        "slug": provider.slug,
        "issuer": provider.issuer,
        "displayName": provider.display_name,
        "status": provider.status,
        "metadata": provider.metadata,
        "createdAt": _iso(provider.created_at),
        "updatedAt": _iso(provider.updated_at),
    }


def api_key_payload(api_key: ProviderApiKeyRecord) -> dict[str, Any]:
    return {
        "id": api_key.id,
        "providerId": api_key.provider_id,
        "name": api_key.name,
        "keyPrefix": api_key.key_prefix,
        "status": api_key.status,
        "expiresAt": _iso(api_key.expires_at),
        "revokedAt": _iso(api_key.revoked_at),
        "lastUsedAt": _iso(api_key.last_used_at),
        "createdAt": _iso(api_key.created_at),
        "updatedAt": _iso(api_key.updated_at),
    }


def assertion_key_payload(assertion_key: ProviderAssertionKeyRecord) -> dict[str, Any]:
    return {
        "id": assertion_key.id,
        "providerId": assertion_key.provider_id,
        "kid": assertion_key.kid,
        "alg": assertion_key.alg,
        "status": assertion_key.status,
        "expiresAt": _iso(assertion_key.expires_at),
        "revokedAt": _iso(assertion_key.revoked_at),
        "createdAt": _iso(assertion_key.created_at),
        "updatedAt": _iso(assertion_key.updated_at),
    }


def _validate(model: type[BaseModel], payload: dict[str, Any]) -> Any:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise IdentityProviderManagementError("invalid_request", str(exc), status=400) from exc


def _validated_id(value: str, code: str, message: str) -> str:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise IdentityProviderManagementError(code, message, status=400) from exc
    return value


def _conflict(code: str, message: str) -> IdentityProviderManagementError:
    return IdentityProviderManagementError(code, message, status=409)


def _not_found(code: str, message: str) -> IdentityProviderManagementError:
    return IdentityProviderManagementError(code, message, status=404)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
