"""Shared principal authentication for invocation and managed-file APIs."""

from __future__ import annotations

from dataclasses import dataclass

from agent_smith.app.invocation import VerifiedActor
from agent_smith.app.services.identity import PrincipalIdentityService
from agent_smith.app.services.provider_auth import IdentityProviderAuthService


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    actor: VerifiedActor
    principal_id: str


class PrincipalAuthenticationService:
    def __init__(
        self,
        provider_auth: IdentityProviderAuthService,
        identities: PrincipalIdentityService,
    ) -> None:
        self._provider_auth = provider_auth
        self._identities = identities

    async def authenticate(
        self,
        *,
        provider_api_key: str | None,
        authorization: str | None,
    ) -> AuthenticatedPrincipal:
        actor = await self._provider_auth.verify_actor(
            provider_api_key=provider_api_key,
            authorization=authorization,
        )
        principal = await self._identities.resolve_principal(actor)
        return AuthenticatedPrincipal(actor=actor, principal_id=str(principal.id))
