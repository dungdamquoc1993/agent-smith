"""Document-processing, derivative, and durable-job contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from agent_smith.app.ports.files import FileRecord

BlockKind = Literal["heading", "paragraph", "table"]
JobStatus = Literal["queued", "running", "retry_wait", "succeeded", "failed", "cancelled"]


class FileProcessingError(RuntimeError):
    """A stable processor failure safe to persist and expose."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class BlockProvenance:
    page: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    section: tuple[str, ...] = ()
    row_start: int | None = None
    row_end: int | None = None


@dataclass(frozen=True)
class NormalizedTable:
    rows: tuple[tuple[str, ...], ...]
    header_rows: int = 0


@dataclass(frozen=True)
class NormalizedBlock:
    id: str
    ordinal: int
    kind: BlockKind
    text: str | None = None
    table: NormalizedTable | None = None
    provenance: BlockProvenance = field(default_factory=BlockProvenance)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedDocument:
    schema_version: str
    file_id: str
    detected_mime_type: str
    blocks: tuple[NormalizedBlock, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedArtifact:
    kind: str
    mime_type: str
    data: bytes
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessingResult:
    """Normalized text/tables plus optional binary page-image derivatives."""

    document: NormalizedDocument
    page_images: tuple[GeneratedArtifact, ...] = ()
    artifacts: tuple[GeneratedArtifact, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


ProgressReporter = Callable[[str, int], Awaitable[None]]


@dataclass(frozen=True)
class ProcessingInput:
    file: FileRecord
    detected_mime_type: str
    data: bytes
    report_progress: ProgressReporter | None = None


class FileProcessor(Protocol):
    name: str
    version: str
    mime_types: frozenset[str]

    async def process(self, value: ProcessingInput) -> ProcessingResult: ...


@dataclass(frozen=True)
class DetectedFile:
    mime_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


class FileTypeDetector(Protocol):
    def detect(self, *, data: bytes, declared_mime_type: str, filename: str) -> DetectedFile: ...


@dataclass(frozen=True)
class DerivativeRecord:
    id: str
    file_id: str
    processing_job_id: str
    kind: str
    object_key: str
    mime_type: str
    size_bytes: int
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class PendingDerivative:
    id: str
    kind: str
    object_key: str
    mime_type: str
    size_bytes: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessingJobRecord:
    id: str
    file_id: str
    pipeline_version: str
    status: JobStatus
    attempts: int
    max_attempts: int
    processor: str | None = None
    phase: str = "queued"
    progress_percent: int = 0
    error: dict[str, Any] | None = None
    available_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FileProcessingStore(Protocol):
    async def mark_uploaded_and_enqueue(
        self,
        *,
        file_id: str,
        principal_id: str,
        etag: str | None,
        sha256: str | None,
        pipeline_version: str,
        max_attempts: int,
    ) -> tuple[FileRecord, ProcessingJobRecord] | None: ...

    async def get_latest_jobs(self, *, file_ids: list[str]) -> dict[str, ProcessingJobRecord]: ...

    async def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> tuple[ProcessingJobRecord, FileRecord] | None: ...

    async def heartbeat(
        self, *, job_id: str, worker_id: str, lease_seconds: int
    ) -> bool: ...

    async def set_detected_type(
        self,
        *,
        job_id: str,
        worker_id: str,
        detected_mime_type: str,
        processor: str,
    ) -> bool: ...

    async def update_progress(
        self,
        *,
        job_id: str,
        worker_id: str,
        phase: str,
        progress_percent: int,
    ) -> bool: ...

    async def schedule_retry(
        self,
        *,
        job_id: str,
        worker_id: str,
        error: dict[str, Any],
        available_at: datetime,
    ) -> bool: ...

    async def fail_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error: dict[str, Any],
    ) -> bool: ...

    async def complete_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        derivatives: list[PendingDerivative],
        processing_metadata: dict[str, Any],
    ) -> bool: ...

    async def list_derivatives(
        self, *, file_id: str, kinds: tuple[str, ...] | None = None
    ) -> list[DerivativeRecord]: ...

    async def cancel_jobs(self, *, file_id: str) -> None: ...

    async def reconcile_uploaded(
        self, *, pipeline_version: str, max_attempts: int, limit: int = 100
    ) -> int: ...
