"""Capability ports consumed by application use cases, exported lazily."""

from importlib import import_module
from typing import Any

_GROUPS = {
    "agent_smith.app.ports.document_processing": (
        "DocumentJobQueue",
        "FileDerivativeReader",
        "FileProcessingRepository",
    ),
    "agent_smith.app.ports.files": (
        "BlobObjectStat",
        "BlobStorageError",
        "BlobStore",
        "FileAuditActor",
        "FileAuditEvent",
        "FileAuditStore",
        "FileAuditUnavailable",
        "FileCatalog",
        "FileCursor",
        "FileMaintenanceStore",
        "FileRecord",
        "FileStatus",
        "PendingFileRecord",
        "PresignedRequest",
    ),
    "agent_smith.app.ports.identity": (
        "IdentityKeyStatus",
        "IdentityPrincipal",
        "IdentityProviderAdminStore",
        "IdentityProviderAuthStore",
        "IdentityProviderRecord",
        "IdentityProviderStatus",
        "IdentityStoreConflictError",
        "PrincipalIdentityStore",
        "ProviderApiKeyRecord",
        "ProviderAssertionKeyRecord",
    ),
    "agent_smith.app.ports.sessions": (
        "PrincipalRecord",
        "PrincipalSessionDirectory",
        "SessionCatalog",
        "SessionRecord",
    ),
}
_EXPORTS = {
    name: module_name
    for module_name, names in _GROUPS.items()
    for name in names
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
