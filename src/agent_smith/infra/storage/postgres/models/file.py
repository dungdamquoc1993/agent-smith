"""Managed file metadata models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agent_smith.infra.storage.postgres.database import Base


class FileStatus(str, enum.Enum):
    pending_upload = "pending_upload"
    uploaded = "uploaded"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    deleted = "deleted"


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_files_size_bytes_positive"),
        Index("ix_files_principal_status_created", "principal_id", "status", "created_at"),
        Index("ix_files_principal_original_name", "principal_id", "original_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    )
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    detected_mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus, name="file_status"),
        nullable=False,
        default=FileStatus.pending_upload,
        server_default=FileStatus.pending_upload.value,
    )
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    processing_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    object_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FileAuditEvent(Base):
    __tablename__ = "file_audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="SET NULL"),
        nullable=True,
    )
    identity_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identity_providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Deliberately not a foreign key: audit survives file metadata purge.
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(100), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index(
    "ix_file_audit_principal_occurred",
    FileAuditEvent.principal_id,
    FileAuditEvent.occurred_at.desc(),
)
Index(
    "ix_file_audit_file_occurred",
    FileAuditEvent.file_id,
    FileAuditEvent.occurred_at.desc(),
)
Index(
    "ix_file_audit_action_occurred",
    FileAuditEvent.action,
    FileAuditEvent.occurred_at.desc(),
)


class ProcessingJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    retry_wait = "retry_wait"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class FileProcessingJob(Base):
    __tablename__ = "file_processing_jobs"
    __table_args__ = (
        Index("ix_file_processing_jobs_claim", "status", "available_at", "lease_expires_at"),
        Index("ix_file_processing_jobs_file_created", "file_id", "created_at"),
        UniqueConstraint("file_id", "pipeline_version", name="uq_file_processing_job_pipeline"),
        CheckConstraint("attempts >= 0", name="ck_file_processing_jobs_attempts"),
        CheckConstraint(
            "progress_percent BETWEEN 0 AND 100", name="ck_file_processing_jobs_progress"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ProcessingJobStatus] = mapped_column(
        Enum(ProcessingJobStatus, name="file_processing_job_status"),
        nullable=False,
        default=ProcessingJobStatus.queued,
        server_default=ProcessingJobStatus.queued.value,
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(default=5, server_default="5", nullable=False)
    processor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phase: Mapped[str] = mapped_column(String(100), default="queued", server_default="queued")
    progress_percent: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FileDerivative(Base):
    __tablename__ = "file_derivatives"
    __table_args__ = (
        Index("ix_file_derivatives_file_kind", "file_id", "kind"),
        CheckConstraint("size_bytes >= 0", name="ck_file_derivatives_size_bytes"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    processing_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("file_processing_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    derivative_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
