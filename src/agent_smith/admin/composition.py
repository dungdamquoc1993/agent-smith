"""Composition root for privileged admin CLI workflows."""

from __future__ import annotations

from agent_smith.admin.config import AdminHttpSettings
from agent_smith.app.services.admin import AdminAuthenticationService, AdminOperatorService
from agent_smith.infra.admin import (
    Argon2AdminPasswordHasher,
    SystemClock,
    UrlSafeTokenGenerator,
)
from agent_smith.infra.storage.postgres import PostgresRuntime
from agent_smith.infra.storage.postgres.adapters import (
    PostgresAdminAuthenticationStore,
    PostgresAdminOperatorStore,
)


class AdminCliContainer:
    def __init__(
        self,
        *,
        operators: AdminOperatorService,
        authentication: AdminAuthenticationService,
        postgres_runtime: PostgresRuntime,
    ) -> None:
        self.operators = operators
        self.authentication = authentication
        self._postgres_runtime = postgres_runtime
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._postgres_runtime.close()


def build_admin_cli_container(settings: AdminHttpSettings | None = None) -> AdminCliContainer:
    resolved_settings = settings or AdminHttpSettings()
    postgres = PostgresRuntime(resolved_settings.postgres_url)
    password_hasher = Argon2AdminPasswordHasher()
    clock = SystemClock()
    operators = AdminOperatorService(
        PostgresAdminOperatorStore(postgres.session_factory),
        password_hasher,
        clock,
    )
    authentication = AdminAuthenticationService(
        PostgresAdminAuthenticationStore(postgres.session_factory),
        password_hasher,
        UrlSafeTokenGenerator(),
        clock,
    )
    return AdminCliContainer(
        operators=operators,
        authentication=authentication,
        postgres_runtime=postgres,
    )
