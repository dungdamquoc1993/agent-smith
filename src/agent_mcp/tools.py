"""Dynamic AgentTool materialization for MCP tools."""

from __future__ import annotations

from agent.types import AgentTool
from ai.types import JsonObject
from permission.tool_specs import MCP_ASK, READ_ONLY_ALLOW

from agent_mcp.naming import mcp_tool_name
from agent_mcp.types import McpServerConfig, McpToolDefinition, McpToolIdentity


def create_mcp_agent_tool(
    manager,
    config: McpServerConfig,
    tool: McpToolDefinition,
    *,
    principal_id: str | None,
) -> AgentTool:
    agent_tool_name = mcp_tool_name(config.name, tool.name)
    identity = McpToolIdentity(
        server_name=config.name,
        tool_name=tool.name,
        agent_tool_name=agent_tool_name,
    )

    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        return await manager.call_tool(
            identity,
            args if isinstance(args, dict) else {},
            principal_id=principal_id,
            timeout_seconds=config.timeout_seconds,
        )

    return AgentTool(
        name=agent_tool_name,
        label=f"{config.name}:{tool.name}",
        description=tool.description or f"MCP tool {tool.name} from {config.name}.",
        parameters=tool.input_schema or _empty_object_schema(),
        execute=execute,
        execution_mode="parallel" if tool.read_only else "sequential",
        permission=READ_ONLY_ALLOW if tool.read_only else MCP_ASK,
    )


def _empty_object_schema() -> JsonObject:
    return {"type": "object", "properties": {}}
