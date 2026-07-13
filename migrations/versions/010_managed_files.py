"""Add managed file metadata catalog.

Revision ID: 010_managed_files
Revises: 009_identity_provider_registry
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "010_managed_files"
down_revision: Union[str, None] = "009_identity_provider_registry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FILE_STATUSES = (
    "pending_upload",
    "uploaded",
    "processing",
    "ready",
    "failed",
    "deleted",
)


def upgrade() -> None:
    bind = op.get_bind()
    file_status = postgresql.ENUM(
        *FILE_STATUSES,
        name="file_status",
        create_type=False,
    )
    file_status.create(bind, checkfirst=True)

    op.create_table(
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_name", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column(
            "status",
            file_status,
            server_default=sa.text("'pending_upload'"),
            nullable=False,
        ),
        sa.Column("etag", sa.String(length=255), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("size_bytes > 0", name="ck_files_size_bytes_positive"),
        sa.UniqueConstraint("object_key", name="uq_files_object_key"),
    )
    op.create_index(
        "ix_files_principal_status_created",
        "files",
        ["principal_id", "status", "created_at"],
    )
    op.create_index(
        "ix_files_principal_original_name",
        "files",
        ["principal_id", "original_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_files_principal_original_name", table_name="files")
    op.drop_index("ix_files_principal_status_created", table_name="files")
    op.drop_table("files")
    postgresql.ENUM(*FILE_STATUSES, name="file_status").drop(op.get_bind(), checkfirst=True)
