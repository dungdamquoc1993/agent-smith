"""AgentTool wrappers for MCP resources."""

from __future__ import annotations

import json
from typing import Any

from agent_smith.core.agent.types import AgentTool, AgentToolResult
from agent_smith.core.llm.types import ImageContent, TextContent

from agent_smith.infra.mcp.content import resource_content_to_agent_block, text_result

LIST_MCP_RESOURCES_TOOL_NAME = "list_mcp_resources"
READ_MCP_RESOURCE_TOOL_NAME = "read_mcp_resource"


def create_list_mcp_resources_tool(
    manager,
    *,
    principal_id: str | None,
) -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        server = args.get("server") if isinstance(args, dict) else None
        resources = await manager.list_resources(principal_id=principal_id, server=server)
        data = [
            resource.model_dump(mode="json", by_alias=True, exclude_none=True)
            for resource in resources
        ]
        return text_result(json.dumps(data, ensure_ascii=False), details={"resources": data})

    return AgentTool(
        name=LIST_MCP_RESOURCES_TOOL_NAME,
        label="List MCP Resources",
        description="List resources from selected connected MCP servers.",
        parameters={
            "type": "object",
            "properties": {
                "server": {"type": "string"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="parallel",
    )


def create_read_mcp_resource_tool(
    manager,
    *,
    principal_id: str | None,
) -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        if not isinstance(args, dict):
            raise ValueError("read_mcp_resource arguments must be an object")
        server = str(args.get("server") or "")
        uri = str(args.get("uri") or "")
        if not server or not uri:
            raise ValueError("server and uri are required")
        contents = await manager.read_resource(principal_id=principal_id, server=server, uri=uri)
        blocks: list[TextContent | ImageContent] = []
        details: dict[str, Any] = {"contents": []}
        for content in contents:
            block, detail = resource_content_to_agent_block(content)
            blocks.append(block)
            details["contents"].append(detail)
        return AgentToolResult(content=blocks, details=details)

    return AgentTool(
        name=READ_MCP_RESOURCE_TOOL_NAME,
        label="Read MCP Resource",
        description="Read a specific MCP resource by server and URI.",
        parameters={
            "type": "object",
            "properties": {
                "server": {"type": "string", "minLength": 1},
                "uri": {"type": "string", "minLength": 1},
            },
            "required": ["server", "uri"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="parallel",
    )
