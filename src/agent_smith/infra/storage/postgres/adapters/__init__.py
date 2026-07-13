"""Capability adapters backed by Postgres."""

from agent_smith.infra.storage.postgres.adapters.identities import PostgresIdentityStore
from agent_smith.infra.storage.postgres.adapters.mcp_credentials import (
    PostgresMcpCredentialStore,
)
from agent_smith.infra.storage.postgres.adapters.resources import PostgresResourceStore
from agent_smith.infra.storage.postgres.adapters.sessions import (
    PostgresRecentConversationProvider,
    PostgresPrincipalSessionDirectory,
    PostgresSessionCatalog,
    PostgresSessionStorage,
)

__all__ = [
    "PostgresIdentityStore",
    "PostgresMcpCredentialStore",
    "PostgresRecentConversationProvider",
    "PostgresPrincipalSessionDirectory",
    "PostgresResourceStore",
    "PostgresSessionCatalog",
    "PostgresSessionStorage",
]
