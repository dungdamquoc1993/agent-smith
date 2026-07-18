"""Postgres managed-file capability adapters, exported lazily."""

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
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
