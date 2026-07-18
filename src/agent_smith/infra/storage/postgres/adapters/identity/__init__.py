"""Postgres identity capability adapters."""

from agent_smith.infra.storage.postgres.adapters.identity.principals import (
    PostgresPrincipalIdentityStore,
)
from agent_smith.infra.storage.postgres.adapters.identity.provider_admin import (
    PostgresIdentityProviderAdminStore,
)
from agent_smith.infra.storage.postgres.adapters.identity.provider_auth import (
    PostgresIdentityProviderAuthStore,
)

__all__ = [
    "PostgresIdentityProviderAdminStore",
    "PostgresIdentityProviderAuthStore",
    "PostgresPrincipalIdentityStore",
]
