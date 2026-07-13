"""MCP runtime support for Agent Smith."""

from agent_smith.infra.mcp.credentials import (
    FernetMcpCredentialCodec,
    McpCredentialCodec,
    McpCredentialStore,
    MemoryMcpCredentialStore,
    generate_mcp_credentials_key,
)
from agent_smith.infra.mcp.errors import McpAuthRequiredError, McpCredentialError, McpRuntimeError
from agent_smith.infra.mcp.manager import (
    McpConnectionManager,
    coerce_server_config,
)
from agent_smith.infra.mcp.resource_tools import (
    LIST_MCP_RESOURCES_TOOL_NAME,
    READ_MCP_RESOURCE_TOOL_NAME,
    create_list_mcp_resources_tool,
    create_read_mcp_resource_tool,
)
from agent_smith.infra.mcp.transports.sdk import SdkMcpTransportFactory
from agent_smith.infra.mcp.naming import mcp_tool_name, mcp_tool_prefix, normalize_mcp_name
from agent_smith.infra.mcp.types import (
    McpClient,
    McpConnectionState,
    McpCredential,
    McpMaterialization,
    McpResourceContent,
    McpResourceDefinition,
    McpServerConfig,
    McpToolCallResult,
    McpToolDefinition,
    McpToolIdentity,
    McpTransportFactory,
)

__all__ = [
    "FernetMcpCredentialCodec",
    "LIST_MCP_RESOURCES_TOOL_NAME",
    "McpAuthRequiredError",
    "McpClient",
    "McpConnectionManager",
    "McpConnectionState",
    "McpCredential",
    "McpCredentialCodec",
    "McpCredentialError",
    "McpCredentialStore",
    "McpMaterialization",
    "McpResourceContent",
    "McpResourceDefinition",
    "McpRuntimeError",
    "McpServerConfig",
    "McpToolCallResult",
    "McpToolDefinition",
    "McpToolIdentity",
    "McpTransportFactory",
    "MemoryMcpCredentialStore",
    "READ_MCP_RESOURCE_TOOL_NAME",
    "SdkMcpTransportFactory",
    "coerce_server_config",
    "create_list_mcp_resources_tool",
    "create_read_mcp_resource_tool",
    "generate_mcp_credentials_key",
    "mcp_tool_name",
    "mcp_tool_prefix",
    "normalize_mcp_name",
]
