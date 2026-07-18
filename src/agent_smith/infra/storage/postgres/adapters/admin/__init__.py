"""Postgres admin identity adapters."""

from agent_smith.infra.storage.postgres.adapters.admin.audit import PostgresAdminAuditReader
from agent_smith.infra.storage.postgres.adapters.admin.authentication import (
    PostgresAdminAuthenticationStore,
)
from agent_smith.infra.storage.postgres.adapters.admin.operators import (
    PostgresAdminOperatorStore,
)

__all__ = [
    "PostgresAdminAuditReader",
    "PostgresAdminAuthenticationStore",
    "PostgresAdminOperatorStore",
]
