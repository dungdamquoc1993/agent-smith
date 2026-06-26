"""Official Python MCP SDK transport adapter."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from pydantic import AnyUrl

from ai.types import JsonObject
from agent_mcp.types import (
    McpClient,
    McpResourceContent,
    McpResourceDefinition,
    McpServerConfig,
    McpToolCallResult,
    McpToolDefinition,
)


class SdkMcpTransportFactory:
    async def connect(
        self,
        config: McpServerConfig,
        *,
        env: dict[str, str],
        headers: dict[str, str],
    ) -> McpClient:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.client.streamable_http import streamablehttp_client

        stack = AsyncExitStack()
        try:
            if config.type == "stdio":
                if not config.command:
                    raise ValueError(f'MCP server "{config.name}" is missing command')
                read_stream, write_stream = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=config.command,
                            args=config.args,
                            env=env or None,
                            cwd=config.cwd,
                        )
                    )
                )
            elif config.type == "http":
                if not config.url:
                    raise ValueError(f'MCP server "{config.name}" is missing url')
                read_stream, write_stream, _ = await stack.enter_async_context(
                    streamablehttp_client(
                        config.url,
                        headers=headers or None,
                        timeout=config.timeout_seconds,
                    )
                )
            else:
                raise ValueError(f"Unsupported MCP transport: {config.type}")

            session = await stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=config.timeout_seconds),
                )
            )
            initialized = await asyncio.wait_for(session.initialize(), timeout=config.timeout_seconds)
            return SdkMcpClient(session=session, stack=stack, capabilities=initialized.capabilities)
        except Exception:
            await stack.aclose()
            raise


class SdkMcpClient:
    def __init__(self, *, session: Any, stack: AsyncExitStack, capabilities: Any) -> None:
        self.session = session
        self.stack = stack
        self.supports_tools = bool(getattr(capabilities, "tools", None))
        self.supports_resources = bool(getattr(capabilities, "resources", None))

    async def list_tools(self) -> list[McpToolDefinition]:
        result = await self.session.list_tools()
        return [
            McpToolDefinition(
                name=tool.name,
                description=tool.description,
                input_schema=tool.inputSchema or {"type": "object", "properties": {}},
                read_only=bool(
                    getattr(getattr(tool, "annotations", None), "readOnlyHint", False)
                ),
            )
            for tool in result.tools
        ]

    async def call_tool(
        self,
        name: str,
        arguments: JsonObject,
        *,
        timeout_seconds: float,
    ) -> McpToolCallResult:
        result = await self.session.call_tool(
            name,
            arguments,
            read_timeout_seconds=timedelta(seconds=timeout_seconds),
        )
        return McpToolCallResult(
            content=list(result.content),
            is_error=bool(result.isError),
            structured_content=result.structuredContent,
            meta=result.meta,
        )

    async def list_resources(self) -> list[McpResourceDefinition]:
        result = await self.session.list_resources()
        return [
            McpResourceDefinition(
                server="",
                uri=str(resource.uri),
                name=resource.name,
                description=resource.description,
                mime_type=resource.mimeType,
            )
            for resource in result.resources
        ]

    async def read_resource(self, uri: str) -> list[McpResourceContent]:
        result = await self.session.read_resource(AnyUrl(uri))
        contents: list[McpResourceContent] = []
        for content in result.contents:
            contents.append(
                McpResourceContent(
                    uri=str(content.uri),
                    mime_type=getattr(content, "mimeType", None),
                    text=getattr(content, "text", None),
                    blob=getattr(content, "blob", None),
                )
            )
        return contents

    async def close(self) -> None:
        await self.stack.aclose()
