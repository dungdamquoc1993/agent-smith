"""Managed image attachment validation and provider-bound materialization."""

from __future__ import annotations

import asyncio
import base64
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from agent_smith.app.ports.files import BlobStorageError, BlobStore, FileCatalog, FileRecord
from agent_smith.core.agent.persistence import (
    FileReferenceContent,
    file_reference_marker,
)
from agent_smith.core.agent.types import AgentMessage
from agent_smith.core.llm.types import (
    ImageContent,
    Message,
    Model,
    TextContent,
    ToolResultMessage,
    UserMessage,
)

IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


class AttachmentError(Exception):
    def __init__(self, code: str, message: str, *, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class AttachmentInput(BaseModel):
    file_id: str = Field(alias="fileId")

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("file_id")
    @classmethod
    def validate_file_id(cls, value: str) -> str:
        try:
            return str(uuid.UUID(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("fileId must be a UUID") from exc


@dataclass(frozen=True)
class ResolvedAttachments:
    records: tuple[FileRecord, ...] = ()

    @property
    def references(self) -> list[FileReferenceContent]:
        return [
            FileReferenceContent(
                fileId=record.id,
                mimeType=record.mime_type,
                displayName=record.original_name,
            )
            for record in self.records
        ]

    @property
    def file_ids(self) -> frozenset[str]:
        return frozenset(record.id for record in self.records)


class AttachmentService:
    def __init__(
        self,
        catalog: FileCatalog,
        blobs: BlobStore,
        *,
        max_attachments: int = 8,
        max_materialized_bytes: int = 20 * 1024 * 1024,
        read_concurrency: int = 4,
    ) -> None:
        self._catalog = catalog
        self._blobs = blobs
        self.max_attachments = max_attachments
        self.max_materialized_bytes = max_materialized_bytes
        self._read_concurrency = max(1, read_concurrency)

    async def resolve_current(
        self,
        *,
        principal_id: str,
        raw_attachments: Any,
        model: Model,
    ) -> ResolvedAttachments:
        inputs = self._parse_inputs(raw_attachments)
        if not inputs:
            return ResolvedAttachments()
        if "image" not in model.input:
            raise AttachmentError(
                "model_does_not_support_images",
                "The selected model does not support image input.",
                status=400,
            )

        records: list[FileRecord] = []
        for item in inputs:
            try:
                record = await self._catalog.get_file(
                    file_id=item.file_id,
                    principal_id=principal_id,
                    include_deleted=True,
                )
            except ValueError:
                record = None
            if record is None or record.status == "deleted":
                raise AttachmentError(
                    "attachment_not_found", "Attachment was not found.", status=404
                )
            if record.status != "ready":
                raise AttachmentError(
                    "attachment_not_ready", "Attachment is not ready.", status=409
                )
            if record.mime_type not in IMAGE_MIME_TYPES:
                raise AttachmentError(
                    "unsupported_attachment_type",
                    "Only PNG, JPEG, GIF, and WebP attachments are supported.",
                    status=415,
                )
            records.append(record)

        if sum(record.size_bytes for record in records) > self.max_materialized_bytes:
            raise AttachmentError(
                "attachments_too_large",
                "Image attachments exceed the materialization limit.",
                status=413,
            )
        return ResolvedAttachments(tuple(records))

    def converter(
        self,
        *,
        principal_id: str,
        model: Model,
        current: ResolvedAttachments,
    ):
        async def convert(messages: list[AgentMessage]) -> list[Message]:
            return await self.materialize(
                messages,
                principal_id=principal_id,
                model=model,
                current=current,
            )

        return convert

    async def materialize(
        self,
        messages: list[AgentMessage],
        *,
        principal_id: str,
        model: Model,
        current: ResolvedAttachments | None = None,
    ) -> list[Message]:
        current = current or ResolvedAttachments()
        occurrences: list[tuple[int, int, FileReferenceContent]] = []
        for message_index, message in enumerate(messages):
            content = getattr(message, "content", None)
            if not isinstance(content, list):
                continue
            for block_index, block in enumerate(content):
                if isinstance(block, FileReferenceContent):
                    occurrences.append((message_index, block_index, block))

        current_keys: set[tuple[int, int]] = set()
        for file_id in current.file_ids:
            matching = [item for item in occurrences if item[2].file_id == file_id]
            if matching:
                current_keys.add((matching[-1][0], matching[-1][1]))

        current_records = {record.id: record for record in current.records}
        decisions: dict[tuple[int, int], tuple[FileReferenceContent, FileRecord | None, str | None]] = {}
        remaining = self.max_materialized_bytes
        supports_images = "image" in model.input

        current_occurrences = [
            item for item in occurrences if (item[0], item[1]) in current_keys
        ]
        history_occurrences = [
            item for item in reversed(occurrences) if (item[0], item[1]) not in current_keys
        ]
        for message_index, block_index, reference in [
            *current_occurrences,
            *history_occurrences,
        ]:
            key = (message_index, block_index)
            is_current = key in current_keys
            record = current_records.get(reference.file_id) if is_current else None
            if record is None:
                try:
                    record = await self._catalog.get_file(
                        file_id=reference.file_id,
                        principal_id=principal_id,
                        include_deleted=True,
                    )
                except ValueError:
                    record = None
            reason: str | None = None
            if not supports_images:
                reason = "selected model is text-only"
            elif record is None:
                reason = "file is missing"
            elif record.status == "deleted":
                reason = "file was deleted"
            elif record.status != "ready":
                reason = "file is not ready"
            elif record.mime_type not in IMAGE_MIME_TYPES:
                reason = "unsupported image type"
            elif record.size_bytes > remaining:
                reason = "image budget exceeded"
            else:
                remaining -= record.size_bytes

            if is_current and reason is not None:
                raise AttachmentError(
                    "attachment_materialization_failed",
                    "A current attachment could not be materialized.",
                    status=502,
                )
            decisions[key] = (reference, record, reason)

        semaphore = asyncio.Semaphore(self._read_concurrency)

        async def read_selected(key: tuple[int, int], record: FileRecord) -> tuple[tuple[int, int], bytes | None]:
            try:
                async with semaphore:
                    data = await self._blobs.read_object(
                        object_key=record.object_key,
                        max_bytes=record.size_bytes,
                    )
                if len(data) != record.size_bytes:
                    raise BlobStorageError("Stored object size changed")
                return key, data
            except BlobStorageError:
                if key in current_keys:
                    raise
                return key, None

        selected = [
            read_selected(key, record)
            for key, (_reference, record, reason) in decisions.items()
            if record is not None and reason is None
        ]
        loaded = dict(await asyncio.gather(*selected)) if selected else {}

        result: list[Message] = []
        for message_index, message in enumerate(messages):
            if message.role == "assistant":
                result.append(message.model_copy(deep=True))
                continue
            content = message.content
            if isinstance(content, str):
                result.append(UserMessage(content=content, timestamp=message.timestamp))
                continue
            blocks: list[TextContent | ImageContent] = []
            for block_index, block in enumerate(content):
                if isinstance(block, TextContent) or isinstance(block, ImageContent):
                    blocks.append(block.model_copy(deep=True))
                    continue
                reference, record, reason = decisions[(message_index, block_index)]
                data = loaded.get((message_index, block_index))
                if data is None:
                    blocks.append(
                        TextContent(
                            text=file_reference_marker(
                                reference,
                                reason=reason or "object could not be read",
                            )
                        )
                    )
                else:
                    blocks.append(
                        ImageContent(
                            data=base64.b64encode(data).decode("ascii"),
                            mimeType=record.mime_type if record else reference.mime_type,
                        )
                    )
            if message.role == "user":
                result.append(UserMessage(content=blocks, timestamp=message.timestamp))
            else:
                result.append(
                    ToolResultMessage(
                        toolCallId=message.tool_call_id,
                        toolName=message.tool_name,
                        content=blocks,
                        details=message.details,
                        isError=message.is_error,
                        timestamp=message.timestamp,
                    )
                )
        return result

    def _parse_inputs(self, raw: Any) -> list[AttachmentInput]:
        if raw is None:
            raise AttachmentError(
                "invalid_attachments", "attachments must be an array.", status=400
            )
        if not isinstance(raw, list):
            raise AttachmentError(
                "invalid_attachments", "attachments must be an array.", status=400
            )
        if len(raw) > self.max_attachments:
            raise AttachmentError(
                "too_many_attachments",
                f"At most {self.max_attachments} attachments are allowed.",
                status=400,
            )
        try:
            parsed = [AttachmentInput.model_validate(item) for item in raw]
        except ValidationError as exc:
            raise AttachmentError(
                "invalid_attachments", "Each attachment must contain only a valid fileId.", status=400
            ) from exc
        ids = [item.file_id for item in parsed]
        if len(ids) != len(set(ids)):
            raise AttachmentError(
                "duplicate_attachment", "Duplicate attachments are not allowed.", status=400
            )
        return parsed
