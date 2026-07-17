"""Managed-file upload lifecycle and library use cases."""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_smith.app.ports.files import (
    BlobStorageError,
    BlobStore,
    FileCatalog,
    FileCursor,
    FileRecord,
    FileStatus,
    PendingFileRecord,
    PresignedRequest,
)

DEFAULT_ALLOWED_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)
READY_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
DOWNLOADABLE_STATUSES = frozenset({"uploaded", "processing", "ready"})
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class FileServiceError(Exception):
    def __init__(self, code: str, message: str, *, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class InitiatedUpload:
    file: FileRecord
    upload: PresignedRequest


@dataclass(frozen=True)
class FilePage:
    files: list[FileRecord]
    next_cursor: str | None


class FileService:
    def __init__(
        self,
        catalog: FileCatalog,
        blobs: BlobStore,
        *,
        max_bytes: int,
        presign_ttl_seconds: int,
        pending_ttl_seconds: int = 3600,
        deleted_retention_seconds: int = 7 * 24 * 3600,
        allowed_mime_types: frozenset[str] = DEFAULT_ALLOWED_MIME_TYPES,
    ) -> None:
        self._catalog = catalog
        self._blobs = blobs
        self._max_bytes = max_bytes
        self._presign_ttl_seconds = presign_ttl_seconds
        self._pending_ttl_seconds = pending_ttl_seconds
        self._deleted_retention_seconds = deleted_retention_seconds
        self._allowed_mime_types = allowed_mime_types

    async def initiate_upload(
        self,
        *,
        principal_id: str,
        original_name: str,
        mime_type: str,
        size_bytes: int,
        sha256: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InitiatedUpload:
        name = _validate_filename(original_name)
        normalized_mime = _validate_mime(mime_type, self._allowed_mime_types)
        if size_bytes <= 0:
            raise FileServiceError("invalid_file", "sizeBytes must be positive.", status=400)
        if size_bytes > self._max_bytes:
            raise FileServiceError("file_too_large", "File exceeds the upload limit.", status=413)
        normalized_sha = _validate_sha256(sha256)
        file_metadata = dict(metadata or {})
        try:
            metadata_bytes = json.dumps(file_metadata, ensure_ascii=False).encode()
        except (TypeError, ValueError) as exc:
            raise FileServiceError("invalid_file", "metadata must be JSON.", status=400) from exc
        if len(metadata_bytes) > 16_384:
            raise FileServiceError("invalid_file", "metadata is too large.", status=400)
        file_id = str(uuid.uuid4())
        object_key = f"principals/{principal_id}/files/{file_id}/original"
        record = await self._catalog.create_pending(
            PendingFileRecord(
                id=file_id,
                principal_id=principal_id,
                original_name=name,
                mime_type=normalized_mime,
                size_bytes=size_bytes,
                sha256=normalized_sha,
                object_key=object_key,
                metadata=file_metadata,
            )
        )
        try:
            upload = await self._blobs.create_upload_url(
                object_key=record.object_key,
                mime_type=record.mime_type,
                size_bytes=record.size_bytes,
                sha256=record.sha256,
                expires_in_seconds=self._presign_ttl_seconds,
            )
        except BlobStorageError as exc:
            await self._catalog.mark_failed(
                file_id=file_id,
                principal_id=principal_id,
                reason="upload_url_unavailable",
            )
            raise FileServiceError(
                "storage_unavailable", "Object storage is temporarily unavailable.", status=502
            ) from exc
        return InitiatedUpload(file=record, upload=upload)

    async def complete_upload(self, *, principal_id: str, file_id: str) -> FileRecord:
        record = await self._require_file(principal_id, file_id)
        if record.status in {"uploaded", "processing", "ready"}:
            return record
        if record.status != "pending_upload":
            raise FileServiceError(
                "invalid_file_state", "File cannot be completed in its current state.", status=409
            )
        try:
            stat = await self._blobs.stat(object_key=record.object_key)
            if stat is None:
                raise FileServiceError("invalid_file", "Uploaded object was not found.", status=400)
            if stat.size_bytes != record.size_bytes:
                await self._fail(record, "size_mismatch")
                raise FileServiceError("invalid_file", "Uploaded size does not match.", status=400)
            if record.sha256 and stat.checksum_sha256:
                if record.sha256.lower() != stat.checksum_sha256.lower():
                    await self._fail(record, "checksum_mismatch")
                    raise FileServiceError(
                        "invalid_file", "Uploaded checksum does not match.", status=400
                    )
            first_bytes = await self._blobs.read_range(
                object_key=record.object_key,
                start=0,
                end=min(record.size_bytes - 1, 8191),
            )
            if not _content_matches_mime(first_bytes, record.mime_type):
                await self._fail(record, "mime_mismatch")
                raise FileServiceError(
                    "invalid_file", "Uploaded content type does not match.", status=400
                )
            updated = await self._catalog.mark_uploaded(
                file_id=file_id,
                principal_id=principal_id,
                mime_type=record.mime_type,
                etag=stat.etag,
                sha256=record.sha256 or stat.checksum_sha256,
            )
        except FileServiceError:
            raise
        except BlobStorageError as exc:
            raise FileServiceError(
                "storage_unavailable", "Object storage is temporarily unavailable.", status=502
            ) from exc
        if updated is None:
            current = await self._require_file(principal_id, file_id)
            if current.status in {"uploaded", "processing", "ready"}:
                return current
            raise FileServiceError("invalid_file_state", "File state changed.", status=409)
        if updated.mime_type in READY_IMAGE_MIME_TYPES:
            ready = await self._catalog.mark_ready(
                file_id=updated.id,
                principal_id=updated.principal_id,
            )
            if ready is not None:
                return ready
            current = await self._require_file(principal_id, file_id)
            if current.status == "ready":
                return current
            raise FileServiceError("invalid_file_state", "File state changed.", status=409)
        return updated

    async def list_files(
        self,
        *,
        principal_id: str,
        limit: int = 50,
        cursor: str | None = None,
        status: FileStatus | None = None,
        mime_type: str | None = None,
    ) -> FilePage:
        if limit < 1 or limit > 100:
            raise FileServiceError("invalid_file", "limit must be between 1 and 100.", status=400)
        decoded_cursor = _decode_cursor(cursor) if cursor else None
        rows = await self._catalog.list_files(
            principal_id=principal_id,
            limit=limit + 1,
            cursor=decoded_cursor,
            status=status,
            mime_type=mime_type,
        )
        page = rows[:limit]
        next_cursor = _encode_cursor(page[-1]) if len(rows) > limit and page else None
        return FilePage(files=page, next_cursor=next_cursor)

    async def get_file(self, *, principal_id: str, file_id: str) -> FileRecord:
        return await self._require_file(principal_id, file_id)

    async def create_download_url(self, *, principal_id: str, file_id: str) -> PresignedRequest:
        record = await self._require_file(principal_id, file_id)
        if record.status not in DOWNLOADABLE_STATUSES:
            raise FileServiceError(
                "invalid_file_state", "File is not available for download.", status=409
            )
        try:
            return await self._blobs.create_download_url(
                object_key=record.object_key,
                download_name=record.original_name,
                mime_type=record.mime_type,
                expires_in_seconds=self._presign_ttl_seconds,
            )
        except BlobStorageError as exc:
            raise FileServiceError(
                "storage_unavailable", "Object storage is temporarily unavailable.", status=502
            ) from exc

    async def delete_file(self, *, principal_id: str, file_id: str) -> FileRecord:
        await self._require_file(principal_id, file_id)
        deleted = await self._catalog.soft_delete(
            file_id=file_id,
            principal_id=principal_id,
            deleted_at=datetime.now(UTC),
        )
        if deleted is None:
            raise FileServiceError("file_not_found", "File was not found.", status=404)
        # Physical deletion is deliberately separate: Postgres and S3 cannot share
        # a transaction, and cleanup_deleted_files can retry safely after retention.
        return deleted

    async def cleanup_stale_uploads(self, *, limit: int = 100) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=self._pending_ttl_seconds)
        rows = await self._catalog.list_stale_pending(created_before=cutoff, limit=limit)
        handled = 0
        for row in rows:
            try:
                await self._blobs.delete(object_key=row.object_key)
            except BlobStorageError:
                continue
            if await self._catalog.mark_failed(
                file_id=row.id,
                principal_id=row.principal_id,
                reason="upload_expired",
            ):
                handled += 1
        return handled

    async def cleanup_deleted_files(self, *, limit: int = 100) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=self._deleted_retention_seconds)
        rows = await self._catalog.list_deleted(deleted_before=cutoff, limit=limit)
        handled = 0
        for row in rows:
            marked = row
            if row.object_deleted_at is None:
                try:
                    await self._blobs.delete(object_key=row.object_key)
                except BlobStorageError:
                    continue
                marked = await self._catalog.mark_object_deleted(
                    file_id=row.id,
                    deleted_at=datetime.now(UTC),
                )
                if marked is None:
                    continue
            # FK-bound metadata remains as a tombstone; unbound metadata can be
            # removed immediately after the object deletion is recorded.
            purged = await self._catalog.purge_file(file_id=row.id)
            if row.object_deleted_at is None or purged:
                handled += 1
        return handled

    async def _require_file(self, principal_id: str, file_id: str) -> FileRecord:
        try:
            record = await self._catalog.get_file(file_id=file_id, principal_id=principal_id)
        except ValueError as exc:
            raise FileServiceError("file_not_found", "File was not found.", status=404) from exc
        if record is None:
            # Ownership mismatches deliberately look like absence to avoid ID enumeration.
            raise FileServiceError("file_not_found", "File was not found.", status=404)
        return record

    async def _fail(self, record: FileRecord, reason: str) -> None:
        await self._catalog.mark_failed(
            file_id=record.id,
            principal_id=record.principal_id,
            reason=reason,
        )


def _validate_filename(value: str) -> str:
    name = value.strip()
    if not name or name in {".", ".."} or len(name) > 512:
        raise FileServiceError("invalid_file", "Invalid originalName.", status=400)
    if "/" in name or "\\" in name or any(ord(char) < 32 for char in name):
        raise FileServiceError("invalid_file", "Invalid originalName.", status=400)
    return name


def _validate_mime(value: str, allowed: frozenset[str]) -> str:
    mime = value.strip().lower().split(";", 1)[0]
    if mime not in allowed:
        raise FileServiceError("invalid_file", "Unsupported mimeType.", status=400)
    return mime


def _validate_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    if not SHA256_RE.fullmatch(value):
        raise FileServiceError("invalid_file", "sha256 must contain 64 hex characters.", status=400)
    return value.lower()


def _content_matches_mime(data: bytes, mime_type: str) -> bool:
    signatures = {
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/gif": (b"GIF87a", b"GIF89a"),
        "image/webp": (b"RIFF",),
        "application/pdf": (b"%PDF-",),
        "application/msword": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
        "application/vnd.ms-excel": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
    }
    if mime_type == "image/webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if mime_type in signatures:
        return data.startswith(signatures[mime_type])
    if mime_type in {"text/plain", "text/markdown", "text/csv"}:
        if b"\x00" in data:
            return False
        try:
            data.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    return False


def _encode_cursor(record: FileRecord) -> str:
    if record.created_at is None:
        raise RuntimeError("Persisted file is missing created_at")
    raw = json.dumps({"createdAt": record.created_at.isoformat(), "id": record.id}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> FileCursor:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return FileCursor(created_at=datetime.fromisoformat(payload["createdAt"]), id=payload["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise FileServiceError("invalid_file", "Invalid pagination cursor.", status=400) from exc
