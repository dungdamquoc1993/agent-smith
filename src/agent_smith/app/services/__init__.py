"""Application use-case services with lazy package exports."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AgentRunService": "agent_smith.app.services.agent_runs",
    "AttachmentService": "agent_smith.app.services.attachments",
    "FileMaintenanceService": "agent_smith.app.services.file_maintenance",
    "FileService": "agent_smith.app.services.files",
    "IdentityProviderAuthService": "agent_smith.app.services.provider_auth",
    "IdentityProviderControlService": "agent_smith.app.services.identity_providers",
    "PrincipalAuthenticationService": "agent_smith.app.services.authentication",
    "PrincipalIdentityService": "agent_smith.app.services.identity",
    "ResourceService": "agent_smith.app.services.resources",
    "RuntimeService": "agent_smith.app.services.runtime",
    "SessionService": "agent_smith.app.services.sessions",
    "TaskService": "agent_smith.app.services.tasks",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
