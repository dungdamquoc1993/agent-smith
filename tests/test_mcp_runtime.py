from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from os import getenv
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.core.agent import AgentTool, AgentToolResult, MemorySessionRepo
from agent_smith.infra.mcp import (
    FernetMcpCredentialCodec,
    LIST_MCP_RESOURCES_TOOL_NAME,
    READ_MCP_RESOURCE_TOOL_NAME,
    McpConnectionManager,
    McpConnectionState,
    McpCredential,
    McpCredentialError,
    McpMaterialization,
    McpResourceContent,
    McpResourceDefinition,
    McpServerConfig,
    McpToolCallResult,
    McpToolDefinition,
    MemoryMcpCredentialStore,
    PostgresMcpCredentialStore,
    generate_mcp_credentials_key,
)
from agent_smith.core.llm.models import make_litellm_model
from agent_smith.core.llm.types import ImageContent, TextContent
from agent_smith.infra.db.base import Base
from agent_smith.infra.db.models.mcp import McpCredentialRecord
from agent_smith.core.resources import MemoryResourceStore, ResourceResolver
from agent_smith.core.runtime import AgentFactory, ToolRegistry


class FakeMcpClient:
    def __init__(
        self,
        *,
        tools: list[McpToolDefinition] | None = None,
        resources: list[McpResourceDefinition] | None = None,
        resource_contents: dict[str, list[McpResourceContent]] | None = None,
        call_results: dict[str, McpToolCallResult | Exception] | None = None,
        supports_tools: bool = True,
        supports_resources: bool = False,
    ) -> None:
        self.supports_tools = supports_tools
        self.supports_resources = supports_resources
        self.tools = tools or []
        self.resources = resources or []
        self.resource_contents = resource_contents or {}
        self.call_results = call_results or {}
        self.calls: list[tuple[str, dict[str, Any], float]] = []
        self.closed = False

    async def list_tools(self) -> list[McpToolDefinition]:
        return self.tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> McpToolCallResult:
        self.calls.append((name, arguments, timeout_seconds))
        result = self.call_results.get(name)
        if isinstance(result, Exception):
            raise result
        return result or McpToolCallResult(content=[{"type": "text", "text": "ok"}])

    async def list_resources(self) -> list[McpResourceDefinition]:
        return self.resources

    async def read_resource(self, uri: str) -> list[McpResourceContent]:
        return self.resource_contents.get(uri, [])

    async def close(self) -> None:
        self.closed = True


class FakeTransportFactory:
    def __init__(self, client: FakeMcpClient | None = None, error: Exception | None = None) -> None:
        self.client = client or FakeMcpClient()
        self.error = error
        self.connections: list[dict[str, Any]] = []

    async def connect(
        self,
        config: McpServerConfig,
        *,
        env: dict[str, str],
        headers: dict[str, str],
    ) -> FakeMcpClient:
        self.connections.append({"config": config, "env": dict(env), "headers": dict(headers)})
        if self.error:
            raise self.error
        return self.client


def _native_tool(name: str = "native") -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, args, signal, on_update
        return AgentToolResult(content=[TextContent(text="native")])

    return AgentTool(
        name=name,
        label=name,
        description=f"{name} tool",
        parameters={"type": "object", "properties": {}},
        execute=execute,
    )


@pytest.mark.asyncio
async def test_memory_credential_store_lookup_precedence_and_inactive_records() -> None:
    store = MemoryMcpCredentialStore()
    expired = datetime.now(UTC) - timedelta(seconds=1)

    store.set_credential(
        principal_id=None,
        server_name="github",
        credential={"env": {"TOKEN": "global-server"}},
    )
    store.set_credential(
        principal_id="principal-1",
        server_name="github",
        credential={"env": {"TOKEN": "principal-server"}},
    )
    store.set_credential(
        principal_id=None,
        server_name="github",
        auth_ref="oauth",
        credential={"env": {"TOKEN": "global-auth"}},
    )
    store.set_credential(
        principal_id="principal-1",
        server_name="github",
        auth_ref="oauth",
        credential={"env": {"TOKEN": "exact"}},
    )

    assert (
        await store.get_credential(
            principal_id="principal-1",
            server_name="github",
            auth_ref="oauth",
        )
    ).env == {"TOKEN": "exact"}

    store.set_credential(
        principal_id="principal-1",
        server_name="github",
        auth_ref="oauth",
        credential={"env": {"TOKEN": "expired"}},
        expires_at=expired,
    )
    assert (
        await store.get_credential(
            principal_id="principal-1",
            server_name="github",
            auth_ref="oauth",
        )
    ).env == {"TOKEN": "global-auth"}

    store.set_credential(
        principal_id=None,
        server_name="github",
        auth_ref="oauth",
        credential={"env": {"TOKEN": "disabled"}},
        disabled=True,
    )
    assert (
        await store.get_credential(
            principal_id="principal-1",
            server_name="github",
            auth_ref="oauth",
        )
    ).env == {"TOKEN": "principal-server"}

    store.delete_credential(principal_id="principal-1", server_name="github")
    assert (
        await store.get_credential(
            principal_id="principal-1",
            server_name="github",
            auth_ref="oauth",
        )
    ).env == {"TOKEN": "global-server"}


def test_fernet_mcp_credential_codec_roundtrip_and_invalid_key_failure() -> None:
    codec = FernetMcpCredentialCodec(generate_mcp_credentials_key())
    credential = McpCredential(
        env={"TOKEN": "env-secret"},
        headers={"Authorization": "Bearer header-secret"},
    )

    encrypted = codec.encrypt(credential)

    assert "env-secret" not in encrypted
    assert "header-secret" not in encrypted
    assert codec.decrypt(encrypted) == credential

    wrong_codec = FernetMcpCredentialCodec(generate_mcp_credentials_key())
    with pytest.raises(McpCredentialError, match="Unable to decrypt"):
        wrong_codec.decrypt(encrypted)


@pytest.mark.asyncio
async def test_postgres_mcp_credential_store_roundtrip_when_database_is_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")

    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = PostgresMcpCredentialStore(
            factory,
            fernet_key=generate_mcp_credentials_key(),
        )
        suffix = uuid.uuid4().hex
        server_name = f"github_{suffix}"
        expired = datetime.now(UTC) - timedelta(seconds=1)

        await store.set_credential(
            principal_id=None,
            server_name=server_name,
            credential={"env": {"TOKEN": "global-server"}},
        )
        await store.set_credential(
            principal_id="principal-1",
            server_name=server_name,
            credential={"env": {"TOKEN": "principal-server"}},
        )
        await store.set_credential(
            principal_id=None,
            server_name=server_name,
            auth_ref="oauth",
            credential={"env": {"TOKEN": "global-auth"}},
        )
        await store.set_credential(
            principal_id="principal-1",
            server_name=server_name,
            auth_ref="oauth",
            credential=McpCredential(
                env={"TOKEN": "exact-secret"},
                headers={"Authorization": "Bearer exact-header"},
            ),
        )

        exact = await store.get_credential(
            principal_id="principal-1",
            server_name=server_name,
            auth_ref="oauth",
        )
        assert exact == McpCredential(
            env={"TOKEN": "exact-secret"},
            headers={"Authorization": "Bearer exact-header"},
        )

        async with factory() as db:
            rows = list(
                await db.scalars(
                    select(McpCredentialRecord).where(
                        McpCredentialRecord.server_name == server_name
                    )
                )
            )
        encrypted_payloads = "\n".join(row.encrypted_payload for row in rows)
        assert "exact-secret" not in encrypted_payloads
        assert "exact-header" not in encrypted_payloads

        await store.set_credential(
            principal_id="principal-1",
            server_name=server_name,
            auth_ref="oauth",
            credential={"env": {"TOKEN": "expired"}},
            expires_at=expired,
        )
        assert (
            await store.get_credential(
                principal_id="principal-1",
                server_name=server_name,
                auth_ref="oauth",
            )
        ).env == {"TOKEN": "global-auth"}

        await store.set_credential(
            principal_id=None,
            server_name=server_name,
            auth_ref="oauth",
            credential={"env": {"TOKEN": "disabled"}},
            disabled=True,
        )
        assert (
            await store.get_credential(
                principal_id="principal-1",
                server_name=server_name,
                auth_ref="oauth",
            )
        ).env == {"TOKEN": "principal-server"}

        await store.delete_credential(
            principal_id="principal-1",
            server_name=server_name,
        )
        assert (
            await store.get_credential(
                principal_id="principal-1",
                server_name=server_name,
                auth_ref="oauth",
            )
        ).env == {"TOKEN": "global-server"}

        factory_for_manager = FakeTransportFactory()
        manager = McpConnectionManager(
            credential_store=store,
            transport_factory=factory_for_manager,
        )
        await manager.materialize_tools(
            {
                server_name: {
                    "type": "http",
                    "url": "https://mcp.example",
                    "authRef": "missing-auth-ref",
                    "headers": {"X-Base": "1"},
                }
            },
            principal_id="principal-1",
        )
        assert factory_for_manager.connections[0]["env"] == {"TOKEN": "global-server"}
        assert factory_for_manager.connections[0]["headers"] == {"X-Base": "1"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resource_resolver_keeps_mcp_config_as_pure_config() -> None:
    store = MemoryResourceStore(
        [
            {
                "kind": "mcp_server_config",
                "name": "github",
                "content": {
                    "name": "github",
                    "config": {"type": "stdio", "command": "github-mcp"},
                },
            }
        ]
    )

    resolved = await ResourceResolver([store]).resolve()

    assert resolved.mcp_server_configs == {"github": {"type": "stdio", "command": "github-mcp"}}


@pytest.mark.asyncio
async def test_agent_factory_materializes_mcp_tools_only_in_create_options() -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def materialize_tools(self, server_configs, *, principal_id=None):
            self.calls.append({"server_configs": server_configs, "principal_id": principal_id})
            return McpMaterialization(
                tools=[_native_tool("mcp__github__search")],
                active_tool_names=["mcp__github__search"],
                states=[McpConnectionState(server_name="github", status="connected")],
            )

    store = MemoryResourceStore(
        [
            {
                "kind": "agent_definition",
                "name": "reviewer",
                "content": {
                    "name": "reviewer",
                    "description": "Review",
                    "systemPrompt": "Review carefully.",
                    "mcpServers": ["github"],
                },
            },
            {
                "kind": "mcp_server_config",
                "name": "github",
                "content": {"name": "github", "config": {"command": "github-mcp"}},
            },
        ]
    )
    fake_manager = FakeManager()
    factory = AgentFactory(
        resource_resolver=ResourceResolver([store]),
        tool_registry=ToolRegistry([_native_tool("native")]),
        default_model=make_litellm_model(provider="openai", model_id="gpt-test"),
        mcp_manager=fake_manager,  # type: ignore[arg-type]
    )

    spec = await factory.build_runtime_spec("reviewer")
    session = await MemorySessionRepo().create(principal_id="principal-1")
    options = await factory.create_options("reviewer", session=session)

    assert fake_manager.calls == [
        {"server_configs": {"github": {"command": "github-mcp"}}, "principal_id": "principal-1"}
    ]
    assert [tool.name for tool in spec.tools] == ["native"]
    assert [tool.name for tool in options.tools or []] == ["native", "mcp__github__search"]
    assert options.active_tool_names == ["native", "mcp__github__search"]


@pytest.mark.asyncio
async def test_dynamic_mcp_tool_forwards_args_and_converts_success() -> None:
    fake_client = FakeMcpClient(
        tools=[
            McpToolDefinition(
                name="Search Issues",
                description="Search issues.",
                inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
                readOnly=True,
            )
        ],
        call_results={
            "Search Issues": McpToolCallResult(
                content=[
                    {"type": "text", "text": "found"},
                    {"type": "image", "data": "aW1n", "mimeType": "image/png"},
                ],
                structuredContent={"count": 1},
            )
        },
    )
    manager = McpConnectionManager(transport_factory=FakeTransportFactory(fake_client))

    materialized = await manager.materialize_tools(
        {"GitHub Server": {"command": "github-mcp", "timeoutSeconds": 7}},
        principal_id="principal-1",
    )
    tool = materialized.tools[0]
    result = await tool.execute("call-1", {"query": "bug"}, None, None)

    assert tool.name == "mcp__github_server__search_issues"
    assert tool.parameters["properties"]["query"]["type"] == "string"
    assert tool.execution_mode == "parallel"
    assert fake_client.calls == [("Search Issues", {"query": "bug"}, 7.0)]
    assert result.content[0] == TextContent(text="found")
    assert isinstance(result.content[1], ImageContent)
    assert result.details["structuredContent"] == {"count": 1}


@pytest.mark.asyncio
async def test_dynamic_mcp_tool_raises_on_mcp_error_and_transport_failure() -> None:
    fake_client = FakeMcpClient(
        tools=[McpToolDefinition(name="delete", inputSchema={"type": "object"})],
        call_results={
            "delete": McpToolCallResult(
                content=[{"type": "text", "text": "denied"}],
                isError=True,
            )
        },
    )
    manager = McpConnectionManager(transport_factory=FakeTransportFactory(fake_client))
    materialized = await manager.materialize_tools({"danger": {"command": "danger-mcp"}})

    with pytest.raises(Exception, match="denied"):
        await materialized.tools[0].execute("call-1", {}, None, None)

    failed = McpConnectionManager(
        transport_factory=FakeTransportFactory(error=RuntimeError("Unauthorized 401"))
    )
    failed_materialized = await failed.materialize_tools({"remote": {"type": "http", "url": "https://mcp"}})

    assert failed_materialized.tools == []
    assert failed_materialized.states[0].status == "needs_auth"


@pytest.mark.asyncio
async def test_credential_overlay_and_worker_local_connections() -> None:
    credential_store = MemoryMcpCredentialStore()
    credential_store.set_credential(
        principal_id="principal-1",
        server_name="github",
        auth_ref="github-oauth",
        credential=McpCredential(
            env={"TOKEN": "env-token"},
            headers={"Authorization": "Bearer header-token"},
        ),
    )
    first_factory = FakeTransportFactory()
    second_factory = FakeTransportFactory()
    first = McpConnectionManager(
        credential_store=credential_store,
        transport_factory=first_factory,
    )
    second = McpConnectionManager(
        credential_store=credential_store,
        transport_factory=second_factory,
    )
    config = {
        "github": {
            "type": "http",
            "url": "https://mcp.example",
            "authRef": "github-oauth",
            "env": {"BASE": "1"},
            "headers": {"X-Base": "1"},
        }
    }

    await first.materialize_tools(config, principal_id="principal-1")
    await second.materialize_tools(config, principal_id="principal-1")
    missing = await first.materialize_tools(config, principal_id="principal-2")

    assert first_factory.connections[0]["env"] == {"BASE": "1", "TOKEN": "env-token"}
    assert first_factory.connections[0]["headers"] == {
        "X-Base": "1",
        "Authorization": "Bearer header-token",
    }
    assert len(first_factory.connections) == 1
    assert len(second_factory.connections) == 1
    assert missing.states[0].status == "needs_auth"


@pytest.mark.asyncio
async def test_resource_wrapper_tools_list_and_read_resource_content() -> None:
    binary_blob = "YmluYXJ5"
    fake_client = FakeMcpClient(
        supports_resources=True,
        resources=[
            McpResourceDefinition(
                server="github",
                uri="file://one",
                name="one",
                mimeType="text/plain",
            )
        ],
        resource_contents={
            "file://one": [
                McpResourceContent(uri="file://one", mimeType="text/plain", text="hello"),
                McpResourceContent(uri="file://img", mimeType="image/png", blob="aW1n"),
                McpResourceContent(uri="file://bin", mimeType="application/octet-stream", blob=binary_blob),
            ]
        },
    )
    manager = McpConnectionManager(transport_factory=FakeTransportFactory(fake_client))
    materialized = await manager.materialize_tools({"github": {"command": "github-mcp"}})
    tools = {tool.name: tool for tool in materialized.tools}

    listed = await tools[LIST_MCP_RESOURCES_TOOL_NAME].execute(
        "list-1",
        {"server": "github"},
        None,
        None,
    )
    read = await tools[READ_MCP_RESOURCE_TOOL_NAME].execute(
        "read-1",
        {"server": "github", "uri": "file://one"},
        None,
        None,
    )

    assert listed.details["resources"][0]["server"] == "github"
    assert listed.details["resources"][0]["uri"] == "file://one"
    assert read.content[0] == TextContent(text="hello")
    assert isinstance(read.content[1], ImageContent)
    assert "Binary MCP resource omitted" in read.content[2].text
    assert read.details["contents"][2]["blobSize"] == 6

    with pytest.raises(Exception, match="Unknown MCP server"):
        await tools[LIST_MCP_RESOURCES_TOOL_NAME].execute("list-2", {"server": "missing"}, None, None)
