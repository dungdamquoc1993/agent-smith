"""Worker-local MCP connection management and tool materialization."""

from __future__ import annotations

from agent.types import AgentTool, AgentToolResult
from ai.types import JsonObject

from agent_mcp.credentials import McpCredentialStore
from agent_mcp.content import mcp_content_to_agent_content, mcp_content_to_text
from agent_mcp.errors import McpAuthRequiredError, McpRuntimeError
from agent_mcp.resource_tools import (
    create_list_mcp_resources_tool,
    create_read_mcp_resource_tool,
)
from agent_mcp.tools import create_mcp_agent_tool
from agent_mcp.transports.sdk import SdkMcpTransportFactory
from agent_mcp.types import (
    McpClient,
    McpConnectionState,
    McpMaterialization,
    McpResourceContent,
    McpResourceDefinition,
    McpServerConfig,
    McpToolIdentity,
    McpTransportFactory,
)


class McpConnectionManager:
    def __init__(
        self,
        *,
        credential_store: McpCredentialStore | None = None,
        transport_factory: McpTransportFactory | None = None,
    ) -> None:
        self.credential_store = credential_store
        self.transport_factory = transport_factory or SdkMcpTransportFactory()
        self._clients: dict[tuple[str | None, str, str], McpClient] = {}
        self._configs: dict[tuple[str | None, str], McpServerConfig] = {}
        self._states: dict[tuple[str | None, str], McpConnectionState] = {}

    async def materialize_tools(
        self,
        server_configs: dict[str, JsonObject],
        *,
        principal_id: str | None = None,
    ) -> McpMaterialization:
        tools: list[AgentTool] = []
        states: list[McpConnectionState] = []
        has_resource_server = False

        for server_name, raw_config in server_configs.items():
            config = coerce_server_config(server_name, raw_config)
            self._configs[(principal_id, config.name)] = config
            state = McpConnectionState(server_name=config.name, status="pending")

            try:
                client = await self._ensure_client(principal_id, config)
                state = McpConnectionState(
                    server_name=config.name,
                    status="connected",
                    supports_tools=client.supports_tools,
                    supports_resources=client.supports_resources,
                )
                if client.supports_tools:
                    for tool in await client.list_tools():
                        tools.append(
                            create_mcp_agent_tool(
                                self,
                                config,
                                tool,
                                principal_id=principal_id,
                            )
                        )
                has_resource_server = has_resource_server or client.supports_resources
            except McpAuthRequiredError as exc:
                state = McpConnectionState(
                    server_name=config.name,
                    status="needs_auth",
                    error=str(exc),
                )
            except Exception as exc:
                state = McpConnectionState(
                    server_name=config.name,
                    status="failed",
                    error=str(exc),
                )

            self._states[(principal_id, config.name)] = state
            states.append(state)

        if has_resource_server:
            tools.extend(
                [
                    create_list_mcp_resources_tool(self, principal_id=principal_id),
                    create_read_mcp_resource_tool(self, principal_id=principal_id),
                ]
            )

        return McpMaterialization(
            tools=tools,
            active_tool_names=[tool.name for tool in tools],
            states=states,
        )

    async def call_tool(
        self,
        identity: McpToolIdentity,
        arguments: JsonObject,
        *,
        principal_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AgentToolResult:
        config = self._require_config(principal_id, identity.server_name)
        client = await self._ensure_client(principal_id, config)
        result = await client.call_tool(
            identity.tool_name,
            arguments,
            timeout_seconds=timeout_seconds or config.timeout_seconds,
        )
        if result.is_error:
            raise McpRuntimeError(mcp_content_to_text(result.content) or "MCP tool returned an error")
        return AgentToolResult(
            content=mcp_content_to_agent_content(result.content),
            details={
                "mcp": identity.model_dump(mode="json", by_alias=True),
                "structuredContent": result.structured_content,
                "meta": result.meta,
            },
        )

    async def list_resources(
        self,
        *,
        principal_id: str | None = None,
        server: str | None = None,
    ) -> list[McpResourceDefinition]:
        resources: list[McpResourceDefinition] = []
        for config in self._selected_configs(principal_id, server):
            client = await self._ensure_client(principal_id, config)
            if client.supports_resources:
                resources.extend(
                    [
                        resource.model_copy(update={"server": config.name})
                        for resource in await client.list_resources()
                    ]
                )
        return resources

    async def read_resource(
        self,
        *,
        principal_id: str | None = None,
        server: str,
        uri: str,
    ) -> list[McpResourceContent]:
        config = self._require_config(principal_id, server)
        client = await self._ensure_client(principal_id, config)
        if not client.supports_resources:
            raise McpRuntimeError(f'MCP server "{server}" does not support resources')
        return await client.read_resource(uri)

    async def close_all(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            await client.close()

    async def _ensure_client(self, principal_id: str | None, config: McpServerConfig) -> McpClient:
        credential = None
        if self.credential_store is not None:
            credential = await self.credential_store.get_credential(
                principal_id=principal_id,
                server_name=config.name,
                auth_ref=config.auth_ref,
            )
        if config.auth_ref and credential is None:
            raise McpAuthRequiredError(f'MCP server "{config.name}" requires authRef "{config.auth_ref}"')

        env = dict(config.env)
        headers = dict(config.headers)
        if credential is not None:
            env.update(credential.env)
            headers.update(credential.headers)

        key = (principal_id, config.name, config.model_dump_json(by_alias=True, exclude_none=True))
        if key not in self._clients:
            try:
                self._clients[key] = await self.transport_factory.connect(
                    config,
                    env=env,
                    headers=headers,
                )
            except Exception as exc:
                if _is_auth_error(exc):
                    raise McpAuthRequiredError(str(exc)) from exc
                raise
        return self._clients[key]

    def _selected_configs(
        self,
        principal_id: str | None,
        server: str | None,
    ) -> list[McpServerConfig]:
        configs = [
            config
            for (candidate_principal, _), config in self._configs.items()
            if candidate_principal == principal_id and (server is None or config.name == server)
        ]
        if server is not None and not configs:
            raise McpRuntimeError(f'Unknown MCP server: "{server}"')
        return configs

    def _require_config(self, principal_id: str | None, server: str) -> McpServerConfig:
        config = self._configs.get((principal_id, server))
        if config is None:
            raise McpRuntimeError(f'Unknown MCP server: "{server}"')
        return config


def coerce_server_config(server_name: str, raw_config: JsonObject) -> McpServerConfig:
    data = dict(raw_config)
    data.setdefault("name", server_name)
    if "type" not in data:
        data["type"] = "http" if data.get("url") else "stdio"
    return McpServerConfig.model_validate(data)


def _is_auth_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "401" in text or "unauthorized" in text or "authentication" in text
