"""Standalone Admin HTTP process composition root."""

from __future__ import annotations

from agent_smith.admin.config import AdminHttpSettings
from agent_smith.app.services.admin import AdminAuthenticationService
from agent_smith.app.services.identity_providers import IdentityProviderControlService
from agent_smith.app.services.provider_auth import IdentityProviderSecretCodec
from agent_smith.infra.admin import Argon2AdminPasswordHasher, SystemClock, UrlSafeTokenGenerator
from agent_smith.infra.storage.postgres import PostgresRuntime
from agent_smith.infra.storage.postgres.adapters import (
    PostgresAdminAuditReader,
    PostgresAdminAuthenticationStore,
    PostgresIdentityProviderControlStore,
)


class AdminHttpContainer:
    def __init__(
        self,
        *,
        settings: AdminHttpSettings,
        authentication: AdminAuthenticationService,
        identity_provider_control: IdentityProviderControlService,
        audit_reader: PostgresAdminAuditReader,
        postgres_runtime: PostgresRuntime,
    ) -> None:
        self.settings = settings
        self.authentication = authentication
        self.identity_provider_control = identity_provider_control
        self.audit_reader = audit_reader
        self._postgres_runtime = postgres_runtime
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._postgres_runtime.close()


def build_admin_http_container(
    settings: AdminHttpSettings | None = None,
) -> AdminHttpContainer:
    resolved = settings or AdminHttpSettings()
    postgres = PostgresRuntime(resolved.postgres_url)
    session_factory = postgres.session_factory
    codec = (
        IdentityProviderSecretCodec(resolved.identity_secrets_key)
        if resolved.identity_secrets_key
        else None
    )
    authentication = AdminAuthenticationService(
        PostgresAdminAuthenticationStore(session_factory),
        Argon2AdminPasswordHasher(),
        UrlSafeTokenGenerator(),
        SystemClock(),
    )
    return AdminHttpContainer(
        settings=resolved,
        authentication=authentication,
        identity_provider_control=IdentityProviderControlService(
            PostgresIdentityProviderControlStore(session_factory), secret_codec=codec
        ),
        audit_reader=PostgresAdminAuditReader(session_factory),
        postgres_runtime=postgres,
    )
