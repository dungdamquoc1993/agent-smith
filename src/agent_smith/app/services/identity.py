"""Principal and external identity resolution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.auth import AppAssertionError
from agent_smith.app.invocation import VerifiedActor
from agent_smith.infra.db.models.principal import AppAssertionNonce, ExternalIdentity, Principal


class PrincipalIdentityService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def resolve_principal(self, actor: VerifiedActor) -> Principal:
        await self.consume_nonce(actor)
        try:
            principal = await self._find_or_create_principal(actor)
        except IntegrityError:
            principal = await self._find_or_create_principal(actor)
        await self._touch_identity(actor, principal)
        return principal

    async def consume_nonce(self, actor: VerifiedActor) -> None:
        expires_at = datetime.fromtimestamp(actor.expires_at, tz=UTC)
        async with self._session_factory() as db, db.begin():
            await db.execute(delete(AppAssertionNonce).where(AppAssertionNonce.expires_at < datetime.now(UTC)))
            db.add(
                AppAssertionNonce(
                    id=uuid.uuid4(),
                    issuer=actor.issuer,
                    jti=actor.jti,
                    expires_at=expires_at,
                )
            )
            try:
                await db.flush()
            except IntegrityError as exc:
                raise AppAssertionError("replayed_assertion", "App assertion jti has already been used.") from exc

    async def _find_or_create_principal(self, actor: VerifiedActor) -> Principal:
        provider_id = self._provider_uuid(actor)
        async with self._session_factory() as db, db.begin():
            existing = await self._get_identity(db, provider_id, actor.subject)
            if existing is not None:
                principal = await db.get(Principal, existing.principal_id)
                if principal is None:
                    raise RuntimeError("External identity points to a missing principal.")
                await db.refresh(principal)
                return principal

            principal = Principal(
                id=uuid.uuid4(),
                display_name=actor.actor.display_name or actor.actor.email or actor.subject,
            )
            db.add(principal)
            await db.flush()
            db.add(
                ExternalIdentity(
                    id=uuid.uuid4(),
                    principal_id=principal.id,
                    identity_provider_id=provider_id,
                    subject=actor.subject,
                    email=actor.actor.email,
                    display_name=actor.actor.display_name,
                    identity_metadata=actor.actor.public_claims(),
                    last_seen_at=datetime.now(UTC),
                )
            )
            await db.flush()
            await db.refresh(principal)
            return principal

    async def _touch_identity(self, actor: VerifiedActor, principal: Principal) -> None:
        provider_id = self._provider_uuid(actor)
        async with self._session_factory() as db, db.begin():
            identity = await self._get_identity(db, provider_id, actor.subject)
            if identity is None:
                return
            identity.email = actor.actor.email
            identity.display_name = actor.actor.display_name
            identity.identity_metadata = actor.actor.public_claims()
            identity.last_seen_at = datetime.now(UTC)
            if actor.actor.display_name and principal.display_name != actor.actor.display_name:
                principal_row = await db.get(Principal, principal.id)
                if principal_row is not None:
                    principal_row.display_name = actor.actor.display_name

    async def _get_identity(
        self,
        db: AsyncSession,
        identity_provider_id: uuid.UUID,
        subject: str,
    ) -> ExternalIdentity | None:
        return (
            await db.scalars(
                select(ExternalIdentity).where(
                    ExternalIdentity.identity_provider_id == identity_provider_id,
                    ExternalIdentity.subject == subject,
                )
            )
        ).one_or_none()

    def _provider_uuid(self, actor: VerifiedActor) -> uuid.UUID:
        if not actor.provider_id:
            raise AppAssertionError(
                "identity_provider_required",
                "Verified actor is missing identity provider id.",
            )
        return uuid.UUID(actor.provider_id)
