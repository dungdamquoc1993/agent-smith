"""Postgres adapter for principal identity resolution."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.identity import IdentityPrincipal, IdentityStoreConflictError
from agent_smith.infra.storage.postgres.models.app_assertions import AppAssertionNonce
from agent_smith.infra.storage.postgres.models.identity_providers import ExternalIdentity
from agent_smith.infra.storage.postgres.models.principals import Principal


class PostgresPrincipalIdentityStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def consume_nonce(self, *, issuer: str, jti: str, expires_at: datetime) -> None:
        try:
            async with self._session_factory() as db, db.begin():
                await db.execute(
                    delete(AppAssertionNonce).where(
                        AppAssertionNonce.expires_at < datetime.now(UTC)
                    )
                )
                db.add(
                    AppAssertionNonce(
                        id=uuid.uuid4(), issuer=issuer, jti=jti, expires_at=expires_at
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
            identity = (
                await db.scalars(
                    select(ExternalIdentity).where(
                        ExternalIdentity.identity_provider_id == provider_uuid,
                        ExternalIdentity.subject == subject,
                    )
                )
            ).one_or_none()
            if identity is None:
                principal = Principal(
                    id=uuid.uuid4(), display_name=display_name or email or subject
                )
                db.add(principal)
                await db.flush()
                db.add(
                    ExternalIdentity(
                        id=uuid.uuid4(),
                        principal_id=principal.id,
                        identity_provider_id=provider_uuid,
                        subject=subject,
                        email=email,
                        display_name=display_name,
                        identity_metadata=metadata,
                        last_seen_at=datetime.now(UTC),
                    )
                )
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
