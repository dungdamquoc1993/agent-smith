"""Managed-file upload lifecycle and library use cases."""

from __future__ import annotations

import base64
import asyncio
import binascii
import json
import math
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from collections.abc import Callable

from agent_smith.app.ports.document_processing import (
    FileProcessingError,
    FileProcessingRepository,
    ProcessingJobRecord,
)

from agent_smith.app.ports.files import (
    BlobStorageError,
    BlobStore,
    FileAuditActor,
    FileAuditEvent,
    FileAuditStore,
    FileAuditUnavailable,
    FileCatalog,
    FileCursor,
    FileQuotaExceeded,
    FileRecord,
    FileStatus,
    PendingFileRecord,
    PresignedRequest,
    TooManyPendingUploads,
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
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)
READY_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
DOWNLOADABLE_STATUSES = frozenset({"uploaded", "processing", "ready", "failed"})
REJECTED_ORIGINAL_FAILURES = frozenset(
    {"size_mismatch", "checksum_mismatch", "mime_mismatch", "upload_expired"}
)
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class FileServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class InitiatedUpload:
    file: FileRecord
    upload: PresignedRequest


@dataclass(frozen=True)
class FilePage:
    files: list[FileRecord]
    next_cursor: str | None


@dataclass
class _TokenBucket:
    tokens: float
    updated_at: float


class PrincipalRateLimiter:
    """Single-process token buckets scoped by principal and operation."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[tuple[str, str], _TokenBucket] = {}
        self._lock = asyncio.Lock()

    async def consume(self, *, principal_id: str, operation: str, rate_per_minute: int) -> None:
        if rate_per_minute <= 0:
            return
        refill_per_second = rate_per_minute / 60.0
        now = self._clock()
        async with self._lock:
            key = (principal_id, operation)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _TokenBucket(float(rate_per_minute), now)
                self._buckets[key] = bucket
            else:
                elapsed = max(0.0, now - bucket.updated_at)
                bucket.tokens = min(
                    float(rate_per_minute),
                    bucket.tokens + elapsed * refill_per_second,
                )
                bucket.updated_at = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return
            retry_after = max(1, math.ceil((1.0 - bucket.tokens) / refill_per_second))
        raise FileServiceError(
            "rate_limited",
            "Too many file operations. Retry later.",
            status=429,
            retry_after=retry_after,
        )


class FileService:
    def __init__(
        self,
        catalog: FileCatalog,
        blobs: BlobStore,
        *,
        max_bytes: int,
        presign_ttl_seconds: int,
        allowed_mime_types: frozenset[str] = DEFAULT_ALLOWED_MIME_TYPES,
        processing_repository: FileProcessingRepository | None = None,
        image_inspector: Callable[..., tuple[str, dict[str, Any]]] | None = None,
        processing_pipeline_version: str = "document-v1",
        processing_max_attempts: int = 5,
        audit_store: FileAuditStore | None = None,
        principal_quota_bytes: int = 5 * 1024 * 1024 * 1024,
        max_pending_uploads: int = 10,
        init_rate_per_minute: int = 30,
        complete_rate_per_minute: int = 60,
        rate_limiter: PrincipalRateLimiter | None = None,
    ) -> None:
        self._catalog = catalog
        self._blobs = blobs
        self._max_bytes = max_bytes
        self._presign_ttl_seconds = presign_ttl_seconds
        self._allowed_mime_types = allowed_mime_types
        self._processing_repository = processing_repository
        self._image_inspector = image_inspector
        self._processing_pipeline_version = processing_pipeline_version
        self._processing_max_attempts = processing_max_attempts
        self._audit_store = audit_store or (
            catalog if callable(getattr(catalog, "append", None)) else None
        )
        self._principal_quota_bytes = principal_quota_bytes
        self._max_pending_uploads = max_pending_uploads
        self._init_rate_per_minute = init_rate_per_minute
        self._complete_rate_per_minute = complete_rate_per_minute
        self._rate_limiter = rate_limiter or PrincipalRateLimiter()

    async def initiate_upload(
        self,
        *,
        principal_id: str,
        original_name: str,
        mime_type: str,
        size_bytes: int,
        sha256: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor: FileAuditActor | None = None,
        correlation_id: str | None = None,
    ) -> InitiatedUpload:
        await self._rate_limiter.consume(
            principal_id=principal_id,
            operation="initiate",
            rate_per_minute=self._init_rate_per_minute,
        )
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
        try:
            upload = await self._blobs.create_upload_url(
                object_key=object_key,
                mime_type=normalized_mime,
                size_bytes=size_bytes,
                sha256=normalized_sha,
                expires_in_seconds=self._presign_ttl_seconds,
            )
        except BlobStorageError as exc:
            raise FileServiceError(
                "storage_unavailable", "Object storage is temporarily unavailable.", status=502
            ) from exc
        audit = _audit_event(
            principal_id=principal_id,
            actor=actor,
            file_id=file_id,
            action="file.upload_initiated",
            outcome="succeeded",
            correlation_id=correlation_id,
            mime_type=normalized_mime,
            declared_size=size_bytes,
            resulting_status="pending_upload",
        )
        try:
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
                ),
                quota_bytes=self._principal_quota_bytes,
                max_pending_uploads=self._max_pending_uploads,
                audit=audit,
            )
        except TooManyPendingUploads as exc:
            raise FileServiceError(
                "too_many_pending_uploads",
                "Too many uploads are awaiting completion.",
                status=409,
            ) from exc
        except FileQuotaExceeded as exc:
            raise FileServiceError(
                "storage_quota_exceeded",
                "Principal storage quota would be exceeded.",
                status=409,
            ) from exc
        except FileAuditUnavailable as exc:
            raise _audit_unavailable() from exc
        return InitiatedUpload(file=record, upload=upload)

    async def complete_upload(
        self,
        *,
        principal_id: str,
        file_id: str,
        actor: FileAuditActor | None = None,
        correlation_id: str | None = None,
    ) -> FileRecord:
        await self._rate_limiter.consume(
            principal_id=principal_id,
            operation="complete",
            rate_per_minute=self._complete_rate_per_minute,
        )
        record = await self._require_file(principal_id, file_id)
        if (
            record.status == "uploaded"
            and record.mime_type not in READY_IMAGE_MIME_TYPES
            and self._processing_repository is not None
        ):
            queued = await self._processing_repository.mark_uploaded_and_enqueue(
                file_id=file_id,
                principal_id=principal_id,
                etag=record.etag,
                sha256=record.sha256,
                pipeline_version=self._processing_pipeline_version,
                max_attempts=self._processing_max_attempts,
            )
            return queued[0] if queued else record
        if record.status in {"uploaded", "processing", "ready"}:
            return record
        if record.status != "pending_upload":
            raise FileServiceError(
                "invalid_file_state", "File cannot be completed in its current state.", status=409
            )
        try:
            stat = await self._blobs.stat(object_key=record.object_key)
            if stat is None:
                await self._append_audit(
                    _audit_event(
                        principal_id=principal_id,
                        actor=actor,
                        file_id=file_id,
                        action="file.upload_completed",
                        outcome="failed",
                        correlation_id=correlation_id,
                        mime_type=record.mime_type,
                        declared_size=record.size_bytes,
                        resulting_status=record.status,
                        failure_code="object_missing",
                    )
                )
                raise FileServiceError("invalid_file", "Uploaded object was not found.", status=400)
            if stat.size_bytes != record.size_bytes:
                await self._reject_upload(
                    record,
                    "size_mismatch",
                    actor=actor,
                    correlation_id=correlation_id,
                )
                raise FileServiceError("invalid_file", "Uploaded size does not match.", status=400)
            if record.sha256 and stat.checksum_sha256:
                if record.sha256.lower() != stat.checksum_sha256.lower():
                    await self._reject_upload(
                        record,
                        "checksum_mismatch",
                        actor=actor,
                        correlation_id=correlation_id,
                    )
                    raise FileServiceError(
                        "invalid_file", "Uploaded checksum does not match.", status=400
                    )
            first_bytes = await self._blobs.read_range(
                object_key=record.object_key,
                start=0,
                end=min(record.size_bytes - 1, 8191),
            )
            if not _content_matches_mime(first_bytes, record.mime_type):
                await self._reject_upload(
                    record,
                    "mime_mismatch",
                    actor=actor,
                    correlation_id=correlation_id,
                )
                raise FileServiceError(
                    "invalid_file", "Uploaded content type does not match.", status=400
                )
            processing_metadata: dict[str, Any] | None = None
            detected_mime_type: str | None = None
            if record.mime_type in READY_IMAGE_MIME_TYPES and self._image_inspector is not None:
                data = await self._blobs.read_object(
                    object_key=record.object_key, max_bytes=record.size_bytes
                )
                detected_mime_type, processing_metadata = await asyncio.to_thread(
                    self._image_inspector,
                    data,
                    declared_mime_type=record.mime_type,
                )
            resulting_status = (
                "ready" if record.mime_type in READY_IMAGE_MIME_TYPES else "uploaded"
            )
            audit = _audit_event(
                principal_id=principal_id,
                actor=actor,
                file_id=file_id,
                action="file.upload_completed",
                outcome="succeeded",
                correlation_id=correlation_id,
                mime_type=record.mime_type,
                declared_size=record.size_bytes,
                resulting_status=resulting_status,
            )
            if record.mime_type not in READY_IMAGE_MIME_TYPES and self._processing_repository:
                queued = await self._processing_repository.mark_uploaded_and_enqueue(
                    file_id=file_id,
                    principal_id=principal_id,
                    etag=stat.etag,
                    sha256=record.sha256 or stat.checksum_sha256,
                    pipeline_version=self._processing_pipeline_version,
                    max_attempts=self._processing_max_attempts,
                    audit=audit,
                )
                updated = queued[0] if queued else None
            else:
                updated = await self._catalog.mark_uploaded(
                    file_id=file_id,
                    principal_id=principal_id,
                    mime_type=record.mime_type,
                    etag=stat.etag,
                    sha256=record.sha256 or stat.checksum_sha256,
                    detected_mime_type=detected_mime_type,
                    processing_metadata=processing_metadata,
                    final_status=resulting_status,
                    audit=audit,
                )
        except FileServiceError:
            raise
        except FileProcessingError as exc:
            try:
                await self._fail(
                    record,
                    f"processing_{exc.code}",
                    audit=_audit_event(
                        principal_id=principal_id,
                        actor=actor,
                        file_id=file_id,
                        action="file.upload_completed",
                        outcome="failed",
                        correlation_id=correlation_id,
                        mime_type=record.mime_type,
                        declared_size=record.size_bytes,
                        resulting_status="failed",
                        failure_code=exc.code,
                    ),
                )
            except FileAuditUnavailable as audit_exc:
                raise _audit_unavailable() from audit_exc
            raise FileServiceError("invalid_file", exc.message, status=400) from exc
        except FileAuditUnavailable as exc:
            raise _audit_unavailable() from exc
        except BlobStorageError as exc:
            raise FileServiceError(
                "storage_unavailable", "Object storage is temporarily unavailable.", status=502
            ) from exc
        if updated is None:
            current = await self._require_file(principal_id, file_id)
            if current.status in {"uploaded", "processing", "ready"}:
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

    async def get_processing_jobs(
        self, *, file_ids: list[str]
    ) -> dict[str, ProcessingJobRecord]:
        if self._processing_repository is None:
            return {}
        return await self._processing_repository.get_latest_jobs(file_ids=file_ids)

    async def create_download_url(
        self,
        *,
        principal_id: str,
        file_id: str,
        actor: FileAuditActor | None = None,
        correlation_id: str | None = None,
    ) -> PresignedRequest:
        record = await self._require_file(principal_id, file_id)
        rejected_original = (
            record.status == "failed" and record.failure_reason in REJECTED_ORIGINAL_FAILURES
        )
        if rejected_original and self._processing_repository is not None:
            # A legacy processing failure may share a code with upload validation;
            # the durable job proves the original belongs to the processing path.
            jobs = await self._processing_repository.get_latest_jobs(file_ids=[record.id])
            rejected_original = record.id not in jobs
        if (
            record.status not in DOWNLOADABLE_STATUSES
            or record.object_deleted_at is not None
            or rejected_original
        ):
            raise FileServiceError(
                "invalid_file_state", "File is not available for download.", status=409
            )
        try:
            download = await self._blobs.create_download_url(
                object_key=record.object_key,
                download_name=record.original_name,
                mime_type=record.mime_type,
                expires_in_seconds=self._presign_ttl_seconds,
            )
        except BlobStorageError as exc:
            raise FileServiceError(
                "storage_unavailable", "Object storage is temporarily unavailable.", status=502
            ) from exc
        await self._append_audit(
            _audit_event(
                principal_id=principal_id,
                actor=actor,
                file_id=file_id,
                action="file.download_url_created",
                outcome="succeeded",
                correlation_id=correlation_id,
                mime_type=record.mime_type,
                declared_size=record.size_bytes,
                resulting_status=record.status,
            )
        )
        return download

    async def delete_file(
        self,
        *,
        principal_id: str,
        file_id: str,
        actor: FileAuditActor | None = None,
        correlation_id: str | None = None,
    ) -> FileRecord:
        record = await self._require_file(principal_id, file_id)
        try:
            deleted = await self._catalog.soft_delete(
                file_id=file_id,
                principal_id=principal_id,
                deleted_at=datetime.now(UTC),
                audit=_audit_event(
                    principal_id=principal_id,
                    actor=actor,
                    file_id=file_id,
                    action="file.deleted",
                    outcome="succeeded",
                    correlation_id=correlation_id,
                    mime_type=record.mime_type,
                    declared_size=record.size_bytes,
                    resulting_status="deleted",
                ),
            )
        except FileAuditUnavailable as exc:
            raise _audit_unavailable() from exc
        if deleted is None:
            raise FileServiceError("file_not_found", "File was not found.", status=404)
        if self._processing_repository is not None:
            await self._processing_repository.cancel_jobs(file_id=file_id)
        # Physical deletion is deliberately separate: Postgres and S3 cannot share
        # a transaction, and cleanup_deleted_files can retry safely after retention.
        return deleted

    async def _require_file(self, principal_id: str, file_id: str) -> FileRecord:
        try:
            record = await self._catalog.get_file(file_id=file_id, principal_id=principal_id)
        except ValueError as exc:
            raise FileServiceError("file_not_found", "File was not found.", status=404) from exc
        if record is None:
            # Ownership mismatches deliberately look like absence to avoid ID enumeration.
            raise FileServiceError("file_not_found", "File was not found.", status=404)
        return record

    async def _fail(
        self,
        record: FileRecord,
        reason: str,
        *,
        audit: FileAuditEvent | None = None,
    ) -> None:
        await self._catalog.mark_failed(
            file_id=record.id,
            principal_id=record.principal_id,
            reason=reason,
            audit=audit,
        )

    async def _reject_upload(
        self,
        record: FileRecord,
        reason: str,
        *,
        actor: FileAuditActor | None,
        correlation_id: str | None,
    ) -> None:
        try:
            failed = await self._catalog.mark_failed(
                file_id=record.id,
                principal_id=record.principal_id,
                reason=reason,
                audit=_audit_event(
                    principal_id=record.principal_id,
                    actor=actor,
                    file_id=record.id,
                    action="file.upload_completed",
                    outcome="failed",
                    correlation_id=correlation_id,
                    mime_type=record.mime_type,
                    declared_size=record.size_bytes,
                    resulting_status="failed",
                    failure_code=reason,
                ),
            )
        except FileAuditUnavailable as exc:
            raise _audit_unavailable() from exc
        if failed is None:
            return
        try:
            await self._blobs.delete(object_key=record.object_key)
        except BlobStorageError:
            return
        await self._catalog.mark_object_deleted(
            file_id=record.id,
            deleted_at=datetime.now(UTC),
        )

    async def _append_audit(self, event: FileAuditEvent) -> None:
        if self._audit_store is None:
            return
        try:
            await self._audit_store.append([event])
        except Exception as exc:
            raise _audit_unavailable() from exc


def _audit_event(
    *,
    principal_id: str,
    actor: FileAuditActor | None,
    file_id: str,
    action: str,
    outcome: str,
    correlation_id: str | None,
    mime_type: str,
    declared_size: int,
    resulting_status: str,
    failure_code: str | None = None,
) -> FileAuditEvent:
    details: dict[str, Any] = {
        "mimeType": mime_type,
        "declaredSize": declared_size,
        "resultingStatus": resulting_status,
    }
    if failure_code is not None:
        details["failureCode"] = failure_code
    return FileAuditEvent(
        principal_id=principal_id,
        identity_provider_id=actor.identity_provider_id if actor else None,
        actor_subject=actor.subject if actor else principal_id,
        file_id=file_id,
        action=action,
        outcome=outcome,
        correlation_id=correlation_id,
        details=details,
    )


def _audit_unavailable() -> FileServiceError:
    return FileServiceError(
        "audit_unavailable",
        "File audit storage is temporarily unavailable.",
        status=503,
    )


def _validate_filename(value: str) -> str:
    name = value.strip()
    if not name or name in {".", ".."} or len(name) > 512:
        raise FileServiceError("invalid_file", "Invalid originalName.", status=400)
    if "/" in name or "\\" in name or any(
        unicodedata.category(char) == "Cc" for char in name
    ):
        raise FileServiceError("invalid_file", "Invalid originalName.", status=400)
    return name


def _validate_mime(value: str, allowed: frozenset[str]) -> str:
    mime = value.strip().lower().split(";", 1)[0]
    if mime not in allowed:
        raise FileServiceError(
            "unsupported_file_type",
            "Unsupported mimeType.",
            status=415,
        )
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
