"""Principal and external identity resolution."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_smith.app.ports.identity import (
    IdentityPrincipal,
    IdentityStoreConflictError,
    PrincipalIdentityStore,
)
from agent_smith.app.auth import AppAssertionError
from agent_smith.app.invocation import VerifiedActor


class PrincipalIdentityService:
    def __init__(self, store: PrincipalIdentityStore) -> None:
        self._store = store

    async def resolve_principal(self, actor: VerifiedActor) -> IdentityPrincipal:
        await self.consume_nonce(actor)
        provider_id = self._provider_id(actor)
        return await self._store.resolve_principal(
            provider_id=provider_id,
            subject=actor.subject,
            email=actor.actor.email,
            display_name=actor.actor.display_name,
            metadata=actor.actor.public_claims(),
        )

    async def consume_nonce(self, actor: VerifiedActor) -> None:
        expires_at = datetime.fromtimestamp(actor.expires_at, tz=UTC)
        try:
            await self._store.consume_nonce(
                issuer=actor.issuer,
                jti=actor.jti,
                expires_at=expires_at,
            )
        except IdentityStoreConflictError as exc:
            raise AppAssertionError(
                "replayed_assertion",
                "App assertion jti has already been used.",
            ) from exc

    @staticmethod
    def _provider_id(actor: VerifiedActor) -> str:
        if not actor.provider_id:
            raise AppAssertionError(
                "identity_provider_required",
                "Verified actor is missing identity provider id.",
            )
        return actor.provider_id
