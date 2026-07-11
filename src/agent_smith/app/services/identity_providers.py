"""Identity provider management use-cases."""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.services.provider_auth import (
    IDENTITY_SECRET_ENCRYPTION_SCHEME,
    IdentityProviderSecretCodec,
    generate_provider_api_key,
    hash_provider_api_key,
    provider_api_key_prefix,
)
from agent_smith.infra.db.models.principal import (
    IdentityProvider,
    IdentityProviderApiKey,
    IdentityProviderAssertionKey,
    IdentityProviderKeyStatus,
    IdentityProviderStatus,
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
    status: IdentityProviderStatus = IdentityProviderStatus.active
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
    display_name: str | None = Field(default=None, alias="displayName", min_length=1, max_length=255)
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
        session_factory: async_sessionmaker[AsyncSession],
        *,
        secret_codec: IdentityProviderSecretCodec | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._secret_codec = secret_codec

    async def create_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _validate(CreateIdentityProviderRequest, payload)
        provider = IdentityProvider(
            id=uuid.uuid4(),
            slug=request.slug,
            issuer=request.issuer,
            display_name=request.display_name,
            status=request.status,
            provider_metadata=request.metadata,
        )
        async with self._session_factory() as db, db.begin():
            db.add(provider)
            try:
                await db.flush()
            except IntegrityError as exc:
                raise _conflict("identity_provider_conflict", "Provider slug or issuer already exists.") from exc
            await db.refresh(provider)
        return {"identityProvider": identity_provider_payload(provider)}

    async def list_providers(self) -> dict[str, Any]:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(IdentityProvider).order_by(
                        IdentityProvider.created_at.desc(),
                        IdentityProvider.id,
                    )
                )
            ).all()
        return {"identityProviders": [identity_provider_payload(row) for row in rows]}

    async def get_provider(self, provider_id: str) -> dict[str, Any]:
        provider = await self._require_provider(provider_id)
        return {"identityProvider": identity_provider_payload(provider)}

    async def update_provider(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = _validate(UpdateIdentityProviderRequest, payload)
        provider_uuid = _uuid(provider_id, "invalid_provider_id", "Invalid provider id.")
        async with self._session_factory() as db, db.begin():
            provider = await db.get(IdentityProvider, provider_uuid)
            if provider is None:
                raise _not_found("provider_not_found", "Identity provider was not found.")
            if request.slug is not None:
                provider.slug = request.slug
            if request.issuer is not None:
                provider.issuer = request.issuer
            if request.display_name is not None:
                provider.display_name = request.display_name
            if request.status is not None:
                provider.status = request.status
            if request.metadata is not None:
                provider.provider_metadata = request.metadata
            try:
                await db.flush()
            except IntegrityError as exc:
                raise _conflict("identity_provider_conflict", "Provider slug or issuer already exists.") from exc
            await db.refresh(provider)
        return {"identityProvider": identity_provider_payload(provider)}

    async def create_api_key(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = _validate(CreateProviderApiKeyRequest, payload)
        provider_uuid = _uuid(provider_id, "invalid_provider_id", "Invalid provider id.")
        raw_key = generate_provider_api_key()
        api_key = IdentityProviderApiKey(
            id=uuid.uuid4(),
            provider_id=provider_uuid,
            name=request.name,
            key_hash=hash_provider_api_key(raw_key),
            key_prefix=provider_api_key_prefix(raw_key),
            expires_at=request.expires_at,
        )
        async with self._session_factory() as db, db.begin():
            if await db.get(IdentityProvider, provider_uuid) is None:
                raise _not_found("provider_not_found", "Identity provider was not found.")
            db.add(api_key)
            await db.flush()
            await db.refresh(api_key)
        data = api_key_payload(api_key)
        data["rawKey"] = raw_key
        return {"apiKey": data}

    async def list_api_keys(self, provider_id: str) -> dict[str, Any]:
        provider_uuid = _uuid(provider_id, "invalid_provider_id", "Invalid provider id.")
        async with self._session_factory() as db:
            if await db.get(IdentityProvider, provider_uuid) is None:
                raise _not_found("provider_not_found", "Identity provider was not found.")
            rows = (
                await db.scalars(
                    select(IdentityProviderApiKey)
                    .where(IdentityProviderApiKey.provider_id == provider_uuid)
                    .order_by(IdentityProviderApiKey.created_at.desc(), IdentityProviderApiKey.id)
                )
            ).all()
        return {"apiKeys": [api_key_payload(row) for row in rows]}

    async def revoke_api_key(self, key_id: str) -> dict[str, Any]:
        key_uuid = _uuid(key_id, "invalid_api_key_id", "Invalid API key id.")
        async with self._session_factory() as db, db.begin():
            api_key = await db.get(IdentityProviderApiKey, key_uuid)
            if api_key is None:
                raise _not_found("api_key_not_found", "Provider API key was not found.")
            api_key.status = IdentityProviderKeyStatus.revoked
            api_key.revoked_at = datetime.now(UTC)
            await db.flush()
            await db.refresh(api_key)
        return {"apiKey": api_key_payload(api_key)}

    async def create_assertion_key(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._secret_codec is None:
            raise IdentityProviderManagementError(
                "identity_secrets_key_required",
                "AGENT_SMITH_IDENTITY_SECRETS_KEY is required to create assertion keys.",
                status=500,
            )
        request = _validate(CreateAssertionKeyRequest, payload)
        provider_uuid = _uuid(provider_id, "invalid_provider_id", "Invalid provider id.")
        raw_secret = secrets.token_urlsafe(48)
        assertion_key = IdentityProviderAssertionKey(
            id=uuid.uuid4(),
            provider_id=provider_uuid,
            kid=request.kid,
            alg="HS256",
            encrypted_secret=self._secret_codec.encrypt(raw_secret),
            encryption_scheme=IDENTITY_SECRET_ENCRYPTION_SCHEME,
            expires_at=request.expires_at,
        )
        async with self._session_factory() as db, db.begin():
            if await db.get(IdentityProvider, provider_uuid) is None:
                raise _not_found("provider_not_found", "Identity provider was not found.")
            db.add(assertion_key)
            try:
                await db.flush()
            except IntegrityError as exc:
                raise _conflict("assertion_key_conflict", "Assertion key kid already exists for provider.") from exc
            await db.refresh(assertion_key)
        data = assertion_key_payload(assertion_key)
        data["rawSecret"] = raw_secret
        return {"assertionKey": data}

    async def list_assertion_keys(self, provider_id: str) -> dict[str, Any]:
        provider_uuid = _uuid(provider_id, "invalid_provider_id", "Invalid provider id.")
        async with self._session_factory() as db:
            if await db.get(IdentityProvider, provider_uuid) is None:
                raise _not_found("provider_not_found", "Identity provider was not found.")
            rows = (
                await db.scalars(
                    select(IdentityProviderAssertionKey)
                    .where(IdentityProviderAssertionKey.provider_id == provider_uuid)
                    .order_by(
                        IdentityProviderAssertionKey.created_at.desc(),
                        IdentityProviderAssertionKey.id,
                    )
                )
            ).all()
        return {"assertionKeys": [assertion_key_payload(row) for row in rows]}

    async def revoke_assertion_key(self, key_id: str) -> dict[str, Any]:
        key_uuid = _uuid(key_id, "invalid_assertion_key_id", "Invalid assertion key id.")
        async with self._session_factory() as db, db.begin():
            assertion_key = await db.get(IdentityProviderAssertionKey, key_uuid)
            if assertion_key is None:
                raise _not_found("assertion_key_not_found", "Assertion key was not found.")
            assertion_key.status = IdentityProviderKeyStatus.revoked
            assertion_key.revoked_at = datetime.now(UTC)
            await db.flush()
            await db.refresh(assertion_key)
        return {"assertionKey": assertion_key_payload(assertion_key)}

    async def _require_provider(self, provider_id: str) -> IdentityProvider:
        provider_uuid = _uuid(provider_id, "invalid_provider_id", "Invalid provider id.")
        async with self._session_factory() as db:
            provider = await db.get(IdentityProvider, provider_uuid)
            if provider is None:
                raise _not_found("provider_not_found", "Identity provider was not found.")
            return provider


def identity_provider_payload(provider: IdentityProvider) -> dict[str, Any]:
    return {
        "id": str(provider.id),
        "slug": provider.slug,
        "issuer": provider.issuer,
        "displayName": provider.display_name,
        "status": _enum_value(provider.status),
        "metadata": provider.provider_metadata or {},
        "createdAt": _iso(provider.created_at),
        "updatedAt": _iso(provider.updated_at),
    }


def api_key_payload(api_key: IdentityProviderApiKey) -> dict[str, Any]:
    return {
        "id": str(api_key.id),
        "providerId": str(api_key.provider_id),
        "name": api_key.name,
        "keyPrefix": api_key.key_prefix,
        "status": _enum_value(api_key.status),
        "expiresAt": _iso(api_key.expires_at),
        "revokedAt": _iso(api_key.revoked_at),
        "lastUsedAt": _iso(api_key.last_used_at),
        "createdAt": _iso(api_key.created_at),
        "updatedAt": _iso(api_key.updated_at),
    }


def assertion_key_payload(assertion_key: IdentityProviderAssertionKey) -> dict[str, Any]:
    return {
        "id": str(assertion_key.id),
        "providerId": str(assertion_key.provider_id),
        "kid": assertion_key.kid,
        "alg": assertion_key.alg,
        "status": _enum_value(assertion_key.status),
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


def _uuid(value: str, code: str, message: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise IdentityProviderManagementError(code, message, status=400) from exc


def _conflict(code: str, message: str) -> IdentityProviderManagementError:
    return IdentityProviderManagementError(code, message, status=409)


def _not_found(code: str, message: str) -> IdentityProviderManagementError:
    return IdentityProviderManagementError(code, message, status=404)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)
