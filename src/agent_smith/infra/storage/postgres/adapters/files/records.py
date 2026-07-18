"""Managed-file capability-private row mappers."""

from agent_smith.app.ports.document_processing import (
    DerivativeRecord,
    ProcessingJobRecord,
)
from agent_smith.app.ports.files import FileRecord
from agent_smith.infra.storage.postgres.models.file_processing import (
    FileDerivative,
    FileProcessingJob,
)
from agent_smith.infra.storage.postgres.models.files import File


def file_record(row: File) -> FileRecord:
    return FileRecord(
        id=str(row.id),
        principal_id=str(row.principal_id),
        original_name=row.original_name,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        object_key=row.object_key,
        status=row.status.value,
        etag=row.etag,
        failure_reason=row.failure_reason,
        metadata=dict(row.file_metadata or {}),
        detected_mime_type=row.detected_mime_type,
        processing_metadata=dict(row.processing_metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
        object_deleted_at=row.object_deleted_at,
    )


def job_record(row: FileProcessingJob) -> ProcessingJobRecord:
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


def derivative_record(row: FileDerivative) -> DerivativeRecord:
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
