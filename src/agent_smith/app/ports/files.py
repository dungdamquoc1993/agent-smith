"""Managed file catalog and blob-storage contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

FileStatus = Literal[
    "pending_upload",
    "uploaded",
    "processing",
    "ready",
    "failed",
    "deleted",
]


class BlobStorageError(RuntimeError):
    """A storage backend operation failed without exposing SDK details."""


class FileAuditUnavailable(RuntimeError):
    """A required audit event could not be durably persisted."""


class FileQuotaExceeded(RuntimeError):
    """A principal has no remaining original-object quota."""


class TooManyPendingUploads(RuntimeError):
    """A principal already has the maximum number of pending uploads."""


@dataclass(frozen=True)
class FileAuditActor:
    subject: str
    identity_provider_id: str | None = None


@dataclass(frozen=True)
class FileAuditEvent:
    principal_id: str | None
    identity_provider_id: str | None
    actor_subject: str
    file_id: str | None
    action: str
    outcome: str
    details: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class FileRecord:
    id: str
    principal_id: str
    original_name: str
    mime_type: str
    size_bytes: int
    object_key: str
    status: FileStatus
    sha256: str | None = None
    etag: str | None = None
    failure_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    detected_mime_type: str | None = None
    processing_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    object_deleted_at: datetime | None = None


@dataclass(frozen=True)
class PendingFileRecord:
    id: str
    principal_id: str
    original_name: str
    mime_type: str
    size_bytes: int
    object_key: str
    sha256: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FileCursor:
    created_at: datetime
    id: str


@dataclass(frozen=True)
class PresignedRequest:
    url: str
    method: Literal["GET", "PUT"]
    expires_at: datetime
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BlobObjectStat:
    size_bytes: int
    etag: str | None = None
    content_type: str | None = None
    checksum_sha256: str | None = None


class FileCatalog(Protocol):
    async def create_pending(
        self,
        file: PendingFileRecord,
        *,
        quota_bytes: int | None = None,
        max_pending_uploads: int | None = None,
        audit: FileAuditEvent | None = None,
    ) -> FileRecord: ...

    async def get_file(
        self,
        *,
        file_id: str,
        principal_id: str,
        include_deleted: bool = False,
    ) -> FileRecord | None: ...

    async def list_files(
        self,
        *,
        principal_id: str,
        limit: int,
        cursor: FileCursor | None = None,
        status: FileStatus | None = None,
        mime_type: str | None = None,
    ) -> list[FileRecord]: ...

    async def mark_uploaded(
        self,
        *,
        file_id: str,
        principal_id: str,
        mime_type: str,
        etag: str | None,
        sha256: str | None,
        detected_mime_type: str | None = None,
        processing_metadata: dict[str, Any] | None = None,
        final_status: Literal["uploaded", "ready"] = "uploaded",
        audit: FileAuditEvent | None = None,
    ) -> FileRecord | None: ...

    async def mark_processing(
        self,
        *,
        file_id: str,
        principal_id: str,
    ) -> FileRecord | None: ...

    async def mark_ready(
        self,
        *,
        file_id: str,
        principal_id: str,
    ) -> FileRecord | None: ...

    async def mark_failed(
        self,
        *,
        file_id: str,
        principal_id: str,
        reason: str,
        pending_only: bool = False,
        audit: FileAuditEvent | None = None,
    ) -> FileRecord | None: ...

    async def soft_delete(
        self,
        *,
        file_id: str,
        principal_id: str,
        deleted_at: datetime,
        audit: FileAuditEvent | None = None,
    ) -> FileRecord | None: ...


class FileMaintenanceStore(Protocol):
    async def mark_expired_upload(
        self, *, file_id: str, principal_id: str
    ) -> FileRecord | None: ...

    async def list_stale_pending(
        self,
        *,
        created_before: datetime,
        limit: int,
    ) -> list[FileRecord]: ...

    async def list_deleted(
        self,
        *,
        deleted_before: datetime,
        limit: int,
    ) -> list[FileRecord]: ...

    async def list_rejected_objects(self, *, limit: int) -> list[FileRecord]: ...

    async def purge_file(self, *, file_id: str) -> bool: ...

    async def mark_object_deleted(
        self, *, file_id: str, deleted_at: datetime
    ) -> FileRecord | None: ...

    async def purge_audit_events_before(
        self, *, occurred_before: datetime, limit: int
    ) -> int: ...


class FileAuditStore(Protocol):
    async def append(self, events: list[FileAuditEvent]) -> None: ...


class BlobStore(Protocol):
    async def create_upload_url(
        self,
        *,
        object_key: str,
        mime_type: str,
        size_bytes: int,
        sha256: str | None,
        expires_in_seconds: int,
    ) -> PresignedRequest: ...

    async def create_download_url(
        self,
        *,
        object_key: str,
        download_name: str,
        mime_type: str,
        expires_in_seconds: int,
    ) -> PresignedRequest: ...

    async def stat(self, *, object_key: str) -> BlobObjectStat | None: ...

    async def read_range(self, *, object_key: str, start: int, end: int) -> bytes: ...

    async def read_object(self, *, object_key: str, max_bytes: int) -> bytes: ...

    async def write_object(
        self, *, object_key: str, data: bytes, mime_type: str
    ) -> BlobObjectStat: ...

    async def delete(self, *, object_key: str) -> None: ...

    async def delete_prefix(self, *, prefix: str) -> None: ...
