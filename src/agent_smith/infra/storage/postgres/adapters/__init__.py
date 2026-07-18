"""Capability adapters backed by Postgres with lazy package exports."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "PostgresDocumentJobQueue": (
        "agent_smith.infra.storage.postgres.adapters.files.document_jobs"
    ),
    "PostgresFileAuditStore": "agent_smith.infra.storage.postgres.adapters.files.audit",
    "PostgresFileCatalog": "agent_smith.infra.storage.postgres.adapters.files.catalog",
    "PostgresFileDerivativeReader": (
        "agent_smith.infra.storage.postgres.adapters.files.derivatives"
    ),
    "PostgresFileMaintenanceStore": (
        "agent_smith.infra.storage.postgres.adapters.files.maintenance"
    ),
    "PostgresFileProcessingRepository": (
        "agent_smith.infra.storage.postgres.adapters.files.processing"
    ),
    "PostgresIdentityProviderAdminStore": (
        "agent_smith.infra.storage.postgres.adapters.identity.provider_admin"
    ),
    "PostgresIdentityProviderAuthStore": (
        "agent_smith.infra.storage.postgres.adapters.identity.provider_auth"
    ),
    "PostgresPrincipalIdentityStore": (
        "agent_smith.infra.storage.postgres.adapters.identity.principals"
    ),
    "PostgresMcpCredentialStore": (
        "agent_smith.infra.storage.postgres.adapters.mcp_credentials"
    ),
    "PostgresPrincipalSessionDirectory": (
        "agent_smith.infra.storage.postgres.adapters.sessions.directory"
    ),
    "PostgresRecentConversationProvider": (
        "agent_smith.infra.storage.postgres.adapters.sessions.recent_conversations"
    ),
    "PostgresResourceStore": "agent_smith.infra.storage.postgres.adapters.resources",
    "PostgresSessionCatalog": (
        "agent_smith.infra.storage.postgres.adapters.sessions.catalog"
    ),
    "PostgresSessionStorage": (
        "agent_smith.infra.storage.postgres.adapters.sessions.storage"
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
