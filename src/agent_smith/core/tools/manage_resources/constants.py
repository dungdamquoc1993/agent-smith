"""Manage resources tool wire constants."""

from agent_smith.core.resources.types import ResourceKind

MANAGE_RESOURCES_TOOL_NAME = "manage_resources"
RESOURCE_KINDS: tuple[ResourceKind, ...] = (
    "skill",
    "prompt_template",
    "agent_definition",
    "mcp_server_config",
    "user_memory",
)
