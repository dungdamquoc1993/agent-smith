"""Postgres durable file-processing queue and derivative catalog."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.document_processing import (
    DerivativeRecord,
    PendingDerivative,
    ProcessingJobRecord,
)
from agent_smith.app.ports.files import FileAuditEvent, FileAuditUnavailable, FileRecord
from agent_smith.infra.storage.postgres.adapters.file_audit import add_audit_event
from agent_smith.infra.storage.postgres.models.file import (
    File,
    FileDerivative,
    FileProcessingJob,
    FileStatus,
    ProcessingJobStatus,
)


class PostgresFileProcessingStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def mark_uploaded_and_enqueue(
        self,
        *,
        file_id: str,
        principal_id: str,
        etag: str | None,
        sha256: str | None,
        pipeline_version: str,
        max_attempts: int,
        audit: FileAuditEvent | None = None,
    ) -> tuple[FileRecord, ProcessingJobRecord] | None:
        now = datetime.now(UTC)
        try:
            async with self._session_factory() as db, db.begin():
                file = await db.scalar(
                    select(File)
                    .where(File.id == _uuid(file_id), File.principal_id == _uuid(principal_id))
                    .with_for_update()
                )
                if file is None or file.status not in {
                    FileStatus.pending_upload,
                    FileStatus.uploaded,
                    FileStatus.processing,
                }:
                    return None
                if file.status == FileStatus.pending_upload:
                    file.status = FileStatus.uploaded
                    file.etag = etag
                    file.sha256 = sha256
                    file.updated_at = now
                job = await db.scalar(
                    select(FileProcessingJob).where(
                        FileProcessingJob.file_id == file.id,
                        FileProcessingJob.pipeline_version == pipeline_version,
                    )
                )
                if job is None:
                    job = FileProcessingJob(
                        id=uuid.uuid4(),
                        file_id=file.id,
                        pipeline_version=pipeline_version,
                        status=ProcessingJobStatus.queued,
                        max_attempts=max_attempts,
                        available_at=now,
                    )
                    db.add(job)
                if audit is not None:
                    add_audit_event(db, audit)
                await db.flush()
                await db.refresh(file)
                await db.refresh(job)
                return _file_record(file), _job_record(job)
        except (SQLAlchemyError, ValueError) as exc:
            if audit is not None:
                raise FileAuditUnavailable(
                    "Unable to persist required file audit event"
                ) from exc
            raise

    async def get_latest_jobs(
        self, *, file_ids: list[str]
    ) -> dict[str, ProcessingJobRecord]:
        if not file_ids:
            return {}
        ids = [_uuid(value) for value in file_ids]
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(FileProcessingJob)
                    .where(FileProcessingJob.file_id.in_(ids))
                    .distinct(FileProcessingJob.file_id)
                    .order_by(FileProcessingJob.file_id, FileProcessingJob.created_at.desc())
                )
            ).all()
            return {str(row.file_id): _job_record(row) for row in rows}

    async def claim_next(
        self, *, worker_id: str, lease_seconds: int
    ) -> tuple[ProcessingJobRecord, FileRecord] | None:
        now = datetime.now(UTC)
        claimable = or_(
            and_(
                FileProcessingJob.status.in_(
                    (ProcessingJobStatus.queued, ProcessingJobStatus.retry_wait)
                ),
                FileProcessingJob.available_at <= now,
            ),
            and_(
                FileProcessingJob.status == ProcessingJobStatus.running,
                FileProcessingJob.lease_expires_at < now,
            ),
        )
        async with self._session_factory() as db, db.begin():
            exhausted_ids = select(FileProcessingJob.id).where(
                FileProcessingJob.status == ProcessingJobStatus.running,
                FileProcessingJob.lease_expires_at < now,
                FileProcessingJob.attempts >= FileProcessingJob.max_attempts,
            )
            await db.execute(
                update(FileProcessingJob)
                .where(FileProcessingJob.id.in_(exhausted_ids))
                .values(
                    status=ProcessingJobStatus.failed,
                    phase="failed",
                    error={
                        "code": "worker_lease_expired",
                        "message": "Processing worker stopped before completion.",
                        "retryable": True,
                    },
                    completed_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
            exhausted_files = select(FileProcessingJob.file_id).where(
                FileProcessingJob.status == ProcessingJobStatus.failed,
                FileProcessingJob.completed_at == now,
            )
            await db.execute(
                update(File)
                .where(File.id.in_(exhausted_files), File.status == FileStatus.processing)
                .values(
                    status=FileStatus.failed,
                    failure_reason="worker_lease_expired",
                    updated_at=now,
                )
            )
            row = (
                await db.execute(
                    select(FileProcessingJob, File)
                    .join(File, File.id == FileProcessingJob.file_id)
                    .where(
                        claimable,
                        FileProcessingJob.attempts < FileProcessingJob.max_attempts,
                        File.status.in_((FileStatus.uploaded, FileStatus.processing)),
                    )
                    .order_by(FileProcessingJob.available_at, FileProcessingJob.created_at)
                    .limit(1)
                    .with_for_update(of=FileProcessingJob, skip_locked=True)
                )
            ).first()
            if row is None:
                return None
            job, file = row
            job.status = ProcessingJobStatus.running
            job.attempts += 1
            job.phase = "downloading"
            job.progress_percent = 5
            job.error = None
            job.lease_owner = worker_id
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            job.started_at = job.started_at or now
            job.updated_at = now
            if file.status == FileStatus.uploaded:
                file.status = FileStatus.processing
                file.updated_at = now
            await db.flush()
            return _job_record(job), _file_record(file)

    async def heartbeat(
        self, *, job_id: str, worker_id: str, lease_seconds: int
    ) -> bool:
        now = datetime.now(UTC)
        async with self._session_factory() as db, db.begin():
            result = await db.execute(
                update(FileProcessingJob)
                .where(
                    FileProcessingJob.id == _uuid(job_id),
                    FileProcessingJob.status == ProcessingJobStatus.running,
                    FileProcessingJob.lease_owner == worker_id,
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
            )
            return bool(result.rowcount)

    async def set_detected_type(
        self,
        *,
        job_id: str,
        worker_id: str,
        detected_mime_type: str,
        processor: str,
    ) -> bool:
        now = datetime.now(UTC)
        async with self._session_factory() as db, db.begin():
            job = await db.scalar(
                select(FileProcessingJob)
                .where(
                    FileProcessingJob.id == _uuid(job_id),
                    FileProcessingJob.status == ProcessingJobStatus.running,
                    FileProcessingJob.lease_owner == worker_id,
                )
                .with_for_update()
            )
            if job is None:
                return False
            job.processor = processor
            job.phase = "detecting"
            job.progress_percent = 15
            job.updated_at = now
            await db.execute(
                update(File)
                .where(File.id == job.file_id, File.status == FileStatus.processing)
                .values(detected_mime_type=detected_mime_type, updated_at=now)
            )
            return True

    async def update_progress(
        self,
        *,
        job_id: str,
        worker_id: str,
        phase: str,
        progress_percent: int,
    ) -> bool:
        now = datetime.now(UTC)
        async with self._session_factory() as db, db.begin():
            result = await db.execute(
                update(FileProcessingJob)
                .where(
                    FileProcessingJob.id == _uuid(job_id),
                    FileProcessingJob.status == ProcessingJobStatus.running,
                    FileProcessingJob.lease_owner == worker_id,
                )
                .values(
                    phase=phase,
                    progress_percent=max(0, min(100, progress_percent)),
                    updated_at=now,
                )
            )
            return bool(result.rowcount)

    async def schedule_retry(
        self,
        *,
        job_id: str,
        worker_id: str,
        error: dict[str, Any],
        available_at: datetime,
    ) -> bool:
        now = datetime.now(UTC)
        async with self._session_factory() as db, db.begin():
            job = await self._owned_job(db, job_id, worker_id)
            if job is None:
                return False
            if job.attempts >= job.max_attempts:
                return await self._fail_locked(db, job, error, now)
            job.status = ProcessingJobStatus.retry_wait
            job.phase = "retry_wait"
            job.error = error
            job.available_at = available_at
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now
            return True

    async def fail_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error: dict[str, Any],
    ) -> bool:
        now = datetime.now(UTC)
        async with self._session_factory() as db, db.begin():
            job = await self._owned_job(db, job_id, worker_id)
            if job is None:
                return False
            return await self._fail_locked(db, job, error, now)

    async def complete_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        derivatives: list[PendingDerivative],
        processing_metadata: dict[str, Any],
    ) -> bool:
        now = datetime.now(UTC)
        async with self._session_factory() as db, db.begin():
            job = await self._owned_job(db, job_id, worker_id)
            if job is None:
                return False
            file = await db.scalar(select(File).where(File.id == job.file_id).with_for_update())
            if file is None or file.status != FileStatus.processing:
                job.status = ProcessingJobStatus.cancelled
                job.phase = "cancelled"
                job.completed_at = now
                job.lease_owner = None
                job.lease_expires_at = None
                return False
            for value in derivatives:
                statement = insert(FileDerivative).values(
                    id=_uuid(value.id),
                    file_id=file.id,
                    processing_job_id=job.id,
                    kind=value.kind,
                    object_key=value.object_key,
                    mime_type=value.mime_type,
                    size_bytes=value.size_bytes,
                    derivative_metadata=value.metadata,
                    updated_at=now,
                )
                await db.execute(
                    statement.on_conflict_do_update(
                        index_elements=[FileDerivative.object_key],
                        set_={
                            "processing_job_id": job.id,
                            "mime_type": value.mime_type,
                            "size_bytes": value.size_bytes,
                            "metadata": value.metadata,
                            "updated_at": now,
                        },
                    )
                )
            job.status = ProcessingJobStatus.succeeded
            job.phase = "completed"
            job.progress_percent = 100
            job.error = None
            job.completed_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now
            file.status = FileStatus.ready
            file.processing_metadata = processing_metadata
            file.failure_reason = None
            file.updated_at = now
            return True

    async def list_derivatives(
        self, *, file_id: str, kinds: tuple[str, ...] | None = None
    ) -> list[DerivativeRecord]:
        conditions: list[Any] = [
            FileDerivative.file_id == _uuid(file_id),
            FileProcessingJob.status == ProcessingJobStatus.succeeded,
        ]
        if kinds:
            conditions.append(FileDerivative.kind.in_(kinds))
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(FileDerivative)
                    .join(FileProcessingJob, FileProcessingJob.id == FileDerivative.processing_job_id)
                    .where(*conditions)
                    .order_by(FileDerivative.created_at, FileDerivative.id)
                )
            ).all()
            return [_derivative_record(row) for row in rows]

    async def cancel_jobs(self, *, file_id: str) -> None:
        now = datetime.now(UTC)
        async with self._session_factory() as db, db.begin():
            await db.execute(
                update(FileProcessingJob)
                .where(
                    FileProcessingJob.file_id == _uuid(file_id),
                    FileProcessingJob.status.in_(
                        (
                            ProcessingJobStatus.queued,
                            ProcessingJobStatus.running,
                            ProcessingJobStatus.retry_wait,
                        )
                    ),
                )
                .values(
                    status=ProcessingJobStatus.cancelled,
                    phase="cancelled",
                    lease_owner=None,
                    lease_expires_at=None,
                    completed_at=now,
                    updated_at=now,
                )
            )

    async def reconcile_uploaded(
        self, *, pipeline_version: str, max_attempts: int, limit: int = 100
    ) -> int:
        supported = (
            "text/plain",
            "text/markdown",
            "text/csv",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        legacy = ("application/msword", "application/vnd.ms-excel")
        now = datetime.now(UTC)
        handled = 0
        async with self._session_factory() as db, db.begin():
            files = (
                await db.scalars(
                    select(File)
                    .where(
                        File.status == FileStatus.uploaded,
                        File.mime_type.in_((*supported, *legacy)),
                    )
                    .order_by(File.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for file in files:
                if file.mime_type in legacy:
                    file.status = FileStatus.failed
                    file.failure_reason = "unsupported_legacy_format"
                    file.updated_at = now
                    handled += 1
                    continue
                existing = await db.scalar(
                    select(FileProcessingJob.id).where(
                        FileProcessingJob.file_id == file.id,
                        FileProcessingJob.pipeline_version == pipeline_version,
                    )
                )
                if existing is not None:
                    continue
                db.add(
                    FileProcessingJob(
                        id=uuid.uuid4(),
                        file_id=file.id,
                        pipeline_version=pipeline_version,
                        status=ProcessingJobStatus.queued,
                        max_attempts=max_attempts,
                        available_at=now,
                    )
                )
                handled += 1
        return handled

    async def _owned_job(
        self, db: AsyncSession, job_id: str, worker_id: str
    ) -> FileProcessingJob | None:
        return await db.scalar(
            select(FileProcessingJob)
            .where(
                FileProcessingJob.id == _uuid(job_id),
                FileProcessingJob.status == ProcessingJobStatus.running,
                FileProcessingJob.lease_owner == worker_id,
            )
            .with_for_update()
        )

    async def _fail_locked(
        self,
        db: AsyncSession,
        job: FileProcessingJob,
        error: dict[str, Any],
        now: datetime,
    ) -> bool:
        job.status = ProcessingJobStatus.failed
        job.phase = "failed"
        job.error = error
        job.completed_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        job.updated_at = now
        await db.execute(
            update(File)
            .where(File.id == job.file_id, File.status == FileStatus.processing)
            .values(
                status=FileStatus.failed,
                failure_reason=str(error.get("code") or "processing_failed")[:4000],
                updated_at=now,
            )
        )
        return True


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("Invalid UUID") from exc


def _file_record(row: File) -> FileRecord:
    return FileRecord(
        id=str(row.id),
        principal_id=str(row.principal_id),
        original_name=row.original_name,
        mime_type=row.mime_type,
        detected_mime_type=row.detected_mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        object_key=row.object_key,
        status=row.status.value,
        etag=row.etag,
        failure_reason=row.failure_reason,
        metadata=dict(row.file_metadata or {}),
        processing_metadata=dict(row.processing_metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
        object_deleted_at=row.object_deleted_at,
    )


def _job_record(row: FileProcessingJob) -> ProcessingJobRecord:
    return ProcessingJobRecord(
        id=str(row.id),
        file_id=str(row.file_id),
        pipeline_version=row.pipeline_version,
        status=row.status.value,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        processor=row.processor,
        phase=row.phase,
        progress_percent=row.progress_percent,
        error=dict(row.error) if row.error else None,
        available_at=row.available_at,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _derivative_record(row: FileDerivative) -> DerivativeRecord:
    return DerivativeRecord(
        id=str(row.id),
        file_id=str(row.file_id),
        processing_job_id=str(row.processing_job_id),
        kind=row.kind,
        object_key=row.object_key,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        metadata=dict(row.derivative_metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
