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
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None


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
    async def create_pending(self, file: PendingFileRecord) -> FileRecord: ...

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
    ) -> FileRecord | None: ...

    async def soft_delete(
        self,
        *,
        file_id: str,
        principal_id: str,
        deleted_at: datetime,
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

    async def purge_file(self, *, file_id: str) -> bool: ...


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

    async def delete(self, *, object_key: str) -> None: ...
