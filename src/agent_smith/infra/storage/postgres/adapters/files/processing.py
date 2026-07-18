"""Postgres adapter for HTTP-side file-processing orchestration."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.document_processing import ProcessingJobRecord
from agent_smith.app.ports.files import FileAuditEvent, FileAuditUnavailable, FileRecord
from agent_smith.infra.storage.postgres.adapters.files.audit import add_audit_event
from agent_smith.infra.storage.postgres.adapters.files.records import file_record, job_record
from agent_smith.infra.storage.postgres.models.file_processing import (
    FileProcessingJob,
    ProcessingJobStatus,
)
from agent_smith.infra.storage.postgres.models.files import File, FileStatus


class PostgresFileProcessingRepository:
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
                return file_record(file), job_record(job)
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
            return {str(row.file_id): job_record(row) for row in rows}

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


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("Invalid UUID") from exc
