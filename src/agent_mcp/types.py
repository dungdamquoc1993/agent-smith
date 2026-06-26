"""Types for Agent Smith's MCP runtime layer."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from agent.types import AgentTool
from ai.types import JsonObject

McpTransportType = Literal["stdio", "http"]
McpConnectionStatus = Literal["pending", "connected", "needs_auth", "failed", "disabled"]


class McpServerConfig(BaseModel):
    name: str
    type: McpTransportType = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    auth_ref: str | None = Field(default=None, alias="authRef")
    timeout_seconds: float = Field(default=120, alias="timeoutSeconds")

    model_config = {"populate_by_name": True, "extra": "allow"}


class McpCredential(BaseModel):
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


class McpToolIdentity(BaseModel):
    server_name: str = Field(alias="serverName")
    tool_name: str = Field(alias="toolName")
    agent_tool_name: str = Field(alias="agentToolName")

    model_config = {"populate_by_name": True}


class McpConnectionState(BaseModel):
    server_name: str = Field(alias="serverName")
    status: McpConnectionStatus
    error: str | None = None
    supports_tools: bool = Field(default=False, alias="supportsTools")
    supports_resources: bool = Field(default=False, alias="supportsResources")

    model_config = {"populate_by_name": True}


class McpMaterialization(BaseModel):
    tools: list[AgentTool] = Field(default_factory=list)
    active_tool_names: list[str] = Field(default_factory=list, alias="activeToolNames")
    states: list[McpConnectionState] = Field(default_factory=list)

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
    }


class McpToolDefinition(BaseModel):
    name: str
    description: str | None = None
    input_schema: JsonObject = Field(default_factory=dict, alias="inputSchema")
    read_only: bool = Field(default=False, alias="readOnly")

    model_config = {"populate_by_name": True}


class McpResourceDefinition(BaseModel):
    server: str
    uri: str
    name: str
    description: str | None = None
    mime_type: str | None = Field(default=None, alias="mimeType")

    model_config = {"populate_by_name": True}


class McpResourceContent(BaseModel):
    uri: str
    mime_type: str | None = Field(default=None, alias="mimeType")
    text: str | None = None
    blob: str | None = None

    model_config = {"populate_by_name": True}


class McpToolCallResult(BaseModel):
    content: list[Any] = Field(default_factory=list)
    is_error: bool = Field(default=False, alias="isError")
    structured_content: JsonObject | None = Field(default=None, alias="structuredContent")
    meta: JsonObject | None = None

    model_config = {"populate_by_name": True}


class McpClient(Protocol):
    supports_tools: bool
    supports_resources: bool

    async def list_tools(self) -> list[McpToolDefinition]: ...

    async def call_tool(
        self,
        name: str,
        arguments: JsonObject,
        *,
        timeout_seconds: float,
    ) -> McpToolCallResult: ...

    async def list_resources(self) -> list[McpResourceDefinition]: ...

    async def read_resource(self, uri: str) -> list[McpResourceContent]: ...

    async def close(self) -> None: ...


class McpTransportFactory(Protocol):
    async def connect(
        self,
        config: McpServerConfig,
        *,
        env: dict[str, str],
        headers: dict[str, str],
    ) -> McpClient: ...
