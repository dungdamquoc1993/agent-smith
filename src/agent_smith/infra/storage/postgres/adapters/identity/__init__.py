"""Postgres identity capability adapters."""

from agent_smith.infra.storage.postgres.adapters.identity.principals import (
    PostgresPrincipalIdentityStore,
)
from agent_smith.infra.storage.postgres.adapters.identity.provider_control import (
    PostgresIdentityProviderControlStore,
)
from agent_smith.infra.storage.postgres.adapters.identity.provider_auth import (
    PostgresIdentityProviderAuthStore,
)

__all__ = [
    "PostgresIdentityProviderControlStore",
    "PostgresIdentityProviderAuthStore",
    "PostgresPrincipalIdentityStore",
]
