"""Document-processing job and derivative models."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agent_smith.infra.storage.postgres.database import Base


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
