"""Process-specific composition roots.

Import the HTTP and worker roots from their respective modules so loading one
process graph never imports the other.
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "DocumentWorkerContainer": "agent_smith.bootstrap.document_worker",
    "HttpContainer": "agent_smith.bootstrap.http",
    "build_document_worker_container": "agent_smith.bootstrap.document_worker",
    "build_http_container": "agent_smith.bootstrap.http",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
