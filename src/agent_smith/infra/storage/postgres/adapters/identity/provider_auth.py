"""Postgres adapter for identity-provider authentication."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.identity import (
    IdentityProviderRecord,
    ProviderApiKeyRecord,
    ProviderAssertionKeyRecord,
)
from agent_smith.infra.storage.postgres.adapters.identity.records import (
    api_key_record,
    assertion_key_record,
    provider_record,
)
from agent_smith.infra.storage.postgres.models.identity_providers import (
    IdentityProvider,
    IdentityProviderApiKey,
    IdentityProviderAssertionKey,
    IdentityProviderKeyStatus,
)


class PostgresIdentityProviderAuthStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_provider_api_key(
        self, key_hash: str
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
            return provider_record(provider), api_key_record(api_key)

    async def list_assertion_keys(
        self, provider_id: str
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
            return [assertion_key_record(row) for row in rows]

    async def mark_api_key_used(self, api_key_id: str, used_at: datetime) -> None:
        async with self._session_factory() as db, db.begin():
            row = await db.get(IdentityProviderApiKey, uuid.UUID(api_key_id))
            if row is not None:
                row.last_used_at = used_at
