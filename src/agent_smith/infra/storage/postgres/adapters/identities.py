"""Postgres adapters for principal and identity-provider capabilities."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.identity import (
    IdentityPrincipal,
    IdentityProviderRecord,
    IdentityStoreConflictError,
    ProviderApiKeyRecord,
    ProviderAssertionKeyRecord,
)
from agent_smith.infra.storage.postgres.models.principal import (
    AppAssertionNonce,
    ExternalIdentity,
    IdentityProvider,
    IdentityProviderApiKey,
    IdentityProviderAssertionKey,
    IdentityProviderKeyStatus,
    IdentityProviderStatus,
    Principal,
)


class PostgresIdentityStore:
    """Implements identity ports while keeping SQLAlchemy inside infra."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def consume_nonce(
        self,
        *,
        issuer: str,
        jti: str,
        expires_at: datetime,
    ) -> None:
        try:
            async with self._session_factory() as db, db.begin():
                await db.execute(
                    delete(AppAssertionNonce).where(
                        AppAssertionNonce.expires_at < datetime.now(UTC)
                    )
                )
                db.add(
                    AppAssertionNonce(
                        id=uuid.uuid4(),
                        issuer=issuer,
                        jti=jti,
                        expires_at=expires_at,
                    )
                )
                await db.flush()
        except IntegrityError as exc:
            raise IdentityStoreConflictError("Assertion nonce already exists") from exc

    async def resolve_principal(
        self,
        *,
        provider_id: str,
        subject: str,
        email: str | None,
        display_name: str | None,
        metadata: dict[str, Any],
    ) -> IdentityPrincipal:
        try:
            return await self._resolve_principal_once(
                provider_id=provider_id,
                subject=subject,
                email=email,
                display_name=display_name,
                metadata=metadata,
            )
        except IntegrityError:
            # A concurrent request may have created the external identity first.
            return await self._resolve_principal_once(
                provider_id=provider_id,
                subject=subject,
                email=email,
                display_name=display_name,
                metadata=metadata,
            )

    async def _resolve_principal_once(
        self,
        *,
        provider_id: str,
        subject: str,
        email: str | None,
        display_name: str | None,
        metadata: dict[str, Any],
    ) -> IdentityPrincipal:
        provider_uuid = uuid.UUID(provider_id)
        async with self._session_factory() as db, db.begin():
            identity = await self._get_identity(db, provider_uuid, subject)
            if identity is None:
                principal = Principal(
                    id=uuid.uuid4(),
                    display_name=display_name or email or subject,
                )
                db.add(principal)
                await db.flush()
                identity = ExternalIdentity(
                    id=uuid.uuid4(),
                    principal_id=principal.id,
                    identity_provider_id=provider_uuid,
                    subject=subject,
                    email=email,
                    display_name=display_name,
                    identity_metadata=metadata,
                    last_seen_at=datetime.now(UTC),
                )
                db.add(identity)
            else:
                principal = await db.get(Principal, identity.principal_id)
                if principal is None:
                    raise RuntimeError("External identity points to a missing principal.")
                identity.email = email
                identity.display_name = display_name
                identity.identity_metadata = metadata
                identity.last_seen_at = datetime.now(UTC)
                if display_name and principal.display_name != display_name:
                    principal.display_name = display_name
            await db.flush()
            await db.refresh(principal)
            return IdentityPrincipal(id=str(principal.id), display_name=principal.display_name)

    async def find_provider_api_key(
        self,
        key_hash: str,
    ) -> tuple[IdentityProviderRecord, ProviderApiKeyRecord] | None:
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    select(IdentityProvider, IdentityProviderApiKey)
                    .join(
                        IdentityProviderApiKey,
                        IdentityProviderApiKey.provider_id == IdentityProvider.id,
                    )
                    .where(IdentityProviderApiKey.key_hash == key_hash)
                )
            ).one_or_none()
            if row is None:
                return None
            provider, api_key = row
            return _provider_record(provider), _api_key_record(api_key)

    async def list_assertion_keys(
        self,
        provider_id: str,
    ) -> list[ProviderAssertionKeyRecord]:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(IdentityProviderAssertionKey).where(
                        IdentityProviderAssertionKey.provider_id == uuid.UUID(provider_id),
                        IdentityProviderAssertionKey.status == IdentityProviderKeyStatus.active,
                        IdentityProviderAssertionKey.revoked_at.is_(None),
                    )
                )
            ).all()
            return [_assertion_key_record(row) for row in rows]

    async def mark_api_key_used(self, api_key_id: str, used_at: datetime) -> None:
        async with self._session_factory() as db, db.begin():
            row = await db.get(IdentityProviderApiKey, uuid.UUID(api_key_id))
            if row is not None:
                row.last_used_at = used_at

    async def create_provider(
        self,
        *,
        slug: str,
        issuer: str,
        display_name: str,
        status: str,
        metadata: dict[str, Any],
    ) -> IdentityProviderRecord:
        row = IdentityProvider(
            id=uuid.uuid4(),
            slug=slug,
            issuer=issuer,
            display_name=display_name,
            status=IdentityProviderStatus(status),
            provider_metadata=metadata,
        )
        try:
            async with self._session_factory() as db, db.begin():
                db.add(row)
                await db.flush()
                await db.refresh(row)
                record = _provider_record(row)
        except IntegrityError as exc:
            raise IdentityStoreConflictError("Provider slug or issuer already exists") from exc
        return record

    async def list_providers(self) -> list[IdentityProviderRecord]:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(IdentityProvider).order_by(
                        IdentityProvider.created_at.desc(),
                        IdentityProvider.id,
                    )
                )
            ).all()
            return [_provider_record(row) for row in rows]

    async def get_provider(self, provider_id: str) -> IdentityProviderRecord | None:
        async with self._session_factory() as db:
            row = await db.get(IdentityProvider, uuid.UUID(provider_id))
            return _provider_record(row) if row is not None else None

    async def update_provider(
        self,
        provider_id: str,
        changes: dict[str, Any],
    ) -> IdentityProviderRecord | None:
        try:
            async with self._session_factory() as db, db.begin():
                row = await db.get(IdentityProvider, uuid.UUID(provider_id))
                if row is None:
                    return None
                if "slug" in changes:
                    row.slug = changes["slug"]
                if "issuer" in changes:
                    row.issuer = changes["issuer"]
                if "display_name" in changes:
                    row.display_name = changes["display_name"]
                if "status" in changes:
                    row.status = IdentityProviderStatus(changes["status"])
                if "metadata" in changes:
                    row.provider_metadata = changes["metadata"]
                await db.flush()
                await db.refresh(row)
                record = _provider_record(row)
        except IntegrityError as exc:
            raise IdentityStoreConflictError("Provider slug or issuer already exists") from exc
        return record

    async def create_api_key(
        self,
        *,
        provider_id: str,
        name: str,
        key_hash: str,
        key_prefix: str,
        expires_at: datetime | None,
    ) -> ProviderApiKeyRecord | None:
        provider_uuid = uuid.UUID(provider_id)
        async with self._session_factory() as db, db.begin():
            if await db.get(IdentityProvider, provider_uuid) is None:
                return None
            row = IdentityProviderApiKey(
                id=uuid.uuid4(),
                provider_id=provider_uuid,
                name=name,
                key_hash=key_hash,
                key_prefix=key_prefix,
                expires_at=expires_at,
            )
            db.add(row)
            await db.flush()
            await db.refresh(row)
            return _api_key_record(row)

    async def list_api_keys(self, provider_id: str) -> list[ProviderApiKeyRecord] | None:
        provider_uuid = uuid.UUID(provider_id)
        async with self._session_factory() as db:
            if await db.get(IdentityProvider, provider_uuid) is None:
                return None
            rows = (
                await db.scalars(
                    select(IdentityProviderApiKey)
                    .where(IdentityProviderApiKey.provider_id == provider_uuid)
                    .order_by(IdentityProviderApiKey.created_at.desc(), IdentityProviderApiKey.id)
                )
            ).all()
            return [_api_key_record(row) for row in rows]

    async def revoke_api_key(
        self,
        key_id: str,
        revoked_at: datetime,
    ) -> ProviderApiKeyRecord | None:
        async with self._session_factory() as db, db.begin():
            row = await db.get(IdentityProviderApiKey, uuid.UUID(key_id))
            if row is None:
                return None
            row.status = IdentityProviderKeyStatus.revoked
            row.revoked_at = revoked_at
            await db.flush()
            await db.refresh(row)
            return _api_key_record(row)

    async def create_assertion_key(
        self,
        *,
        provider_id: str,
        kid: str,
        alg: str,
        encrypted_secret: str,
        encryption_scheme: str,
        expires_at: datetime | None,
    ) -> ProviderAssertionKeyRecord | None:
        provider_uuid = uuid.UUID(provider_id)
        try:
            async with self._session_factory() as db, db.begin():
                if await db.get(IdentityProvider, provider_uuid) is None:
                    return None
                row = IdentityProviderAssertionKey(
                    id=uuid.uuid4(),
                    provider_id=provider_uuid,
                    kid=kid,
                    alg=alg,
                    encrypted_secret=encrypted_secret,
                    encryption_scheme=encryption_scheme,
                    expires_at=expires_at,
                )
                db.add(row)
                await db.flush()
                await db.refresh(row)
                record = _assertion_key_record(row)
        except IntegrityError as exc:
            raise IdentityStoreConflictError("Assertion key kid already exists") from exc
        return record

    async def list_provider_assertion_keys(
        self,
        provider_id: str,
    ) -> list[ProviderAssertionKeyRecord] | None:
        provider_uuid = uuid.UUID(provider_id)
        async with self._session_factory() as db:
            if await db.get(IdentityProvider, provider_uuid) is None:
                return None
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
            return [_assertion_key_record(row) for row in rows]

    async def revoke_assertion_key(
        self,
        key_id: str,
        revoked_at: datetime,
    ) -> ProviderAssertionKeyRecord | None:
        async with self._session_factory() as db, db.begin():
            row = await db.get(IdentityProviderAssertionKey, uuid.UUID(key_id))
            if row is None:
                return None
            row.status = IdentityProviderKeyStatus.revoked
            row.revoked_at = revoked_at
            await db.flush()
            await db.refresh(row)
            return _assertion_key_record(row)

    @staticmethod
    async def _get_identity(
        db: AsyncSession,
        provider_id: uuid.UUID,
        subject: str,
    ) -> ExternalIdentity | None:
        return (
            await db.scalars(
                select(ExternalIdentity).where(
                    ExternalIdentity.identity_provider_id == provider_id,
                    ExternalIdentity.subject == subject,
                )
            )
        ).one_or_none()


def _provider_record(row: IdentityProvider) -> IdentityProviderRecord:
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


def _api_key_record(row: IdentityProviderApiKey) -> ProviderApiKeyRecord:
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


def _assertion_key_record(row: IdentityProviderAssertionKey) -> ProviderAssertionKeyRecord:
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
