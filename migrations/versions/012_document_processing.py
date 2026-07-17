"""Add durable document processing jobs and derivatives.

Revision ID: 012_document_processing
Revises: 011_session_entry_files
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "012_document_processing"
down_revision: Union[str, None] = "011_session_entry_files"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JOB_STATUSES = ("queued", "running", "retry_wait", "succeeded", "failed", "cancelled")


def upgrade() -> None:
    bind = op.get_bind()
    job_status = postgresql.ENUM(*JOB_STATUSES, name="file_processing_job_status", create_type=False)
    job_status.create(bind, checkfirst=True)
    op.add_column("files", sa.Column("detected_mime_type", sa.String(255), nullable=True))
    op.add_column(
        "files",
        sa.Column(
            "processing_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_table(
        "file_processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pipeline_version", sa.String(100), nullable=False),
        sa.Column("status", job_status, server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("processor", sa.String(255), nullable=True),
        sa.Column("phase", sa.String(100), server_default="queued", nullable=False),
        sa.Column("progress_percent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("file_id", "pipeline_version", name="uq_file_processing_job_pipeline"),
        sa.CheckConstraint("attempts >= 0", name="ck_file_processing_jobs_attempts"),
        sa.CheckConstraint(
            "progress_percent BETWEEN 0 AND 100", name="ck_file_processing_jobs_progress"
        ),
    )
    op.create_index(
        "ix_file_processing_jobs_claim",
        "file_processing_jobs",
        ["status", "available_at", "lease_expires_at"],
    )
    op.create_index(
        "ix_file_processing_jobs_file_created",
        "file_processing_jobs",
        ["file_id", "created_at"],
    )
    op.create_table(
        "file_derivatives",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "processing_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("file_processing_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("size_bytes >= 0", name="ck_file_derivatives_size_bytes"),
    )
    op.create_index(
        "ix_file_derivatives_file_kind", "file_derivatives", ["file_id", "kind"]
    )


def downgrade() -> None:
    op.drop_index("ix_file_derivatives_file_kind", table_name="file_derivatives")
    op.drop_table("file_derivatives")
    op.drop_index("ix_file_processing_jobs_file_created", table_name="file_processing_jobs")
    op.drop_index("ix_file_processing_jobs_claim", table_name="file_processing_jobs")
    op.drop_table("file_processing_jobs")
    op.drop_column("files", "processing_metadata")
    op.drop_column("files", "detected_mime_type")
    postgresql.ENUM(*JOB_STATUSES, name="file_processing_job_status").drop(
        op.get_bind(), checkfirst=True
    )
