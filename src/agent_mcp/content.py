"""Conversion helpers between MCP SDK content and Agent Smith content."""

from __future__ import annotations

import base64
import json
from typing import Any

from agent.types import AgentToolResult
from ai.types import ImageContent, TextContent

from agent_mcp.types import McpResourceContent


def mcp_content_to_agent_content(content: list[Any]) -> list[TextContent | ImageContent]:
    blocks: list[TextContent | ImageContent] = []
    for entry in content:
        if _content_type(entry) == "text":
            blocks.append(TextContent(text=str(_content_value(entry, "text") or "")))
        elif _content_type(entry) == "image":
            mime_type = str(
                _content_value(entry, "mimeType") or _content_value(entry, "mime_type") or ""
            )
            data = str(_content_value(entry, "data") or "")
            blocks.append(ImageContent(data=data, mimeType=mime_type or "image/png"))
        else:
            blocks.append(TextContent(text=_unknown_content_placeholder(entry)))
    return blocks or [TextContent(text="")]


def mcp_content_to_text(content: list[Any]) -> str:
    return "\n".join(
        block.text
        for block in mcp_content_to_agent_content(content)
        if isinstance(block, TextContent)
    )


def resource_content_to_agent_block(
    content: McpResourceContent,
) -> tuple[TextContent | ImageContent, dict[str, Any]]:
    detail = content.model_dump(mode="json", by_alias=True, exclude_none=True)
    mime_type = content.mime_type or ""
    if content.text is not None:
        return TextContent(text=content.text), detail
    if content.blob and mime_type.startswith("image/"):
        return ImageContent(data=content.blob, mimeType=mime_type), detail
    size = len(base64.b64decode(content.blob)) if content.blob else 0
    return (
        TextContent(
            text=(
                "Binary MCP resource omitted from context "
                f"({mime_type or 'unknown'}, {size} bytes)."
            )
        ),
        {**detail, "blob": None, "blobSize": size},
    )


def text_result(text: str, *, details: Any | None = None) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=text)], details=details)


def _content_type(entry: Any) -> str | None:
    if isinstance(entry, dict):
        return entry.get("type")
    return getattr(entry, "type", None)


def _content_value(entry: Any, key: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


def _unknown_content_placeholder(entry: Any) -> str:
    return "Unsupported MCP content: " + json.dumps(_jsonable(entry), ensure_ascii=False)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return value
    return str(value)
