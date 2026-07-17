"""Strict session-safe message content.

Provider image payloads intentionally do not belong to these models. Managed
images are persisted as immutable references and materialized only at the
provider boundary.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, Field

from agent_smith.core.llm.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolResultMessage,
    UserMessage,
)


class FileReferenceContent(BaseModel):
    type: Literal["fileReference"] = "fileReference"
    file_id: str = Field(alias="fileId")
    mime_type: str = Field(alias="mimeType")
    display_name: str = Field(alias="displayName")

    model_config = {"populate_by_name": True, "extra": "forbid"}


PersistedContentBlock: TypeAlias = TextContent | FileReferenceContent


class PersistedUserMessage(UserMessage):
    content: str | list[PersistedContentBlock]


class PersistedToolResultMessage(ToolResultMessage):
    content: list[PersistedContentBlock]


PersistedMessage: TypeAlias = PersistedUserMessage | AssistantMessage | PersistedToolResultMessage

RUNTIME_IMAGE_MARKER = "[Runtime image omitted from persisted session: {mime_type}]"


def file_reference_marker(reference: FileReferenceContent, *, reason: str | None = None) -> str:
    suffix = f"; {reason}" if reason else ""
    return (
        f"[File attachment: {reference.display_name} "
        f"({reference.mime_type}, fileId={reference.file_id}){suffix}]"
    )


def project_message_for_persistence(
    message: UserMessage | AssistantMessage | ToolResultMessage,
) -> PersistedMessage:
    """Remove runtime-only image bytes before a session entry is constructed."""

    if message.role == "assistant":
        return message.model_copy(deep=True)
    if message.role == "user":
        if isinstance(message.content, str):
            return PersistedUserMessage(content=message.content, timestamp=message.timestamp)
        blocks: list[PersistedContentBlock] = []
        for block in message.content:
            if isinstance(block, TextContent):
                blocks.append(block.model_copy(deep=True))
            elif isinstance(block, FileReferenceContent):
                blocks.append(block.model_copy(deep=True))
            elif isinstance(block, ImageContent):
                blocks.append(TextContent(text=RUNTIME_IMAGE_MARKER.format(mime_type=block.mime_type)))
        return PersistedUserMessage(content=blocks, timestamp=message.timestamp)

    blocks = []
    for block in message.content:
        if isinstance(block, TextContent):
            blocks.append(block.model_copy(deep=True))
        elif isinstance(block, FileReferenceContent):
            blocks.append(block.model_copy(deep=True))
        elif isinstance(block, ImageContent):
            blocks.append(TextContent(text=RUNTIME_IMAGE_MARKER.format(mime_type=block.mime_type)))
    return PersistedToolResultMessage(
        toolCallId=message.tool_call_id,
        toolName=message.tool_name,
        content=blocks,
        details=message.details,
        isError=message.is_error,
        timestamp=message.timestamp,
    )


def persisted_message_to_provider_markers(
    message: UserMessage | AssistantMessage | ToolResultMessage,
):
    """Safe default conversion used when no App materializer is installed."""

    if message.role == "assistant":
        return message.model_copy(deep=True)
    if message.role == "user":
        if isinstance(message.content, str):
            return UserMessage(content=message.content, timestamp=message.timestamp)
        return UserMessage(
            content=[
                block.model_copy(deep=True)
                if isinstance(block, (TextContent, ImageContent))
                else TextContent(text=file_reference_marker(block))
                for block in message.content
            ],
            timestamp=message.timestamp,
        )
    return ToolResultMessage(
        toolCallId=message.tool_call_id,
        toolName=message.tool_name,
        content=[
            block.model_copy(deep=True)
            if isinstance(block, (TextContent, ImageContent))
            else TextContent(text=file_reference_marker(block))
            for block in message.content
        ],
        details=message.details,
        isError=message.is_error,
        timestamp=message.timestamp,
    )
