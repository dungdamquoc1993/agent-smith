"""Postgres adapter for identity-provider administration."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.identity import (
    IdentityProviderRecord,
    IdentityStoreConflictError,
    ProviderApiKeyRecord,
    ProviderAssertionKeyRecord,
)
from agent_smith.infra.storage.postgres.models.identity_providers import (
    IdentityProvider,
    IdentityProviderApiKey,
    IdentityProviderAssertionKey,
    IdentityProviderKeyStatus,
    IdentityProviderStatus,
)
from agent_smith.infra.storage.postgres.adapters.identity.records import (
    api_key_record,
    assertion_key_record,
    provider_record,
)


class PostgresIdentityProviderAdminStore:

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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
                record = provider_record(row)
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
            return [provider_record(row) for row in rows]

    async def get_provider(self, provider_id: str) -> IdentityProviderRecord | None:
        async with self._session_factory() as db:
            row = await db.get(IdentityProvider, uuid.UUID(provider_id))
            return provider_record(row) if row is not None else None

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
                record = provider_record(row)
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
            return api_key_record(row)

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
            return [api_key_record(row) for row in rows]

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
            return api_key_record(row)

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
                record = assertion_key_record(row)
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
            return [assertion_key_record(row) for row in rows]

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
            return assertion_key_record(row)
