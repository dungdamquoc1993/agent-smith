"""Resource catalog and resolution APIs."""

from agent_smith.resources.filesystem import FilesystemResourceStore
from agent_smith.resources.memory import MemoryResourceStore
from agent_smith.resources.postgres import PostgresResourceStore
from agent_smith.resources.resolver import (
    ResolvedResources,
    ResourceResolver,
    agent_definition_from_record,
    mcp_server_config_from_record,
    prompt_template_from_record,
    skill_from_record,
)
from agent_smith.resources.store import (
    ResourceConflictError,
    ResourceNotFoundError,
    ResourceReadOnlyError,
    ResourceStore,
    ResourceStoreError,
)
from agent_smith.resources.types import (
    AgentDefinition,
    AgentModelRef,
    McpServerConfig,
    ResourceCreate,
    ResourceKind,
    ResourceRecord,
    ResourceScope,
    ResourceSourceType,
    ResourceUpdate,
    ResourceVersion,
    resource_content_hash,
)

__all__ = [
    "AgentDefinition",
    "AgentModelRef",
    "FilesystemResourceStore",
    "McpServerConfig",
    "MemoryResourceStore",
    "PostgresResourceStore",
    "ResolvedResources",
    "ResourceConflictError",
    "ResourceCreate",
    "ResourceKind",
    "ResourceNotFoundError",
    "ResourceReadOnlyError",
    "ResourceRecord",
    "ResourceResolver",
    "ResourceScope",
    "ResourceSourceType",
    "ResourceStore",
    "ResourceStoreError",
    "ResourceUpdate",
    "ResourceVersion",
    "agent_definition_from_record",
    "mcp_server_config_from_record",
    "prompt_template_from_record",
    "resource_content_hash",
    "skill_from_record",
]
