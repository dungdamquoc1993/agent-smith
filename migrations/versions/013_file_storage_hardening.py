"""Add durable managed-file audit events.

Revision ID: 013_file_storage_hardening
Revises: 012_document_processing
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "013_file_storage_hardening"
down_revision: Union[str, None] = "012_document_processing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "file_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "identity_provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identity_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # No files FK: the immutable identifier remains after metadata purge.
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_subject", sa.String(512), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(100), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_file_audit_principal_occurred",
        "file_audit_events",
        ["principal_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_file_audit_file_occurred",
        "file_audit_events",
        ["file_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_file_audit_action_occurred",
        "file_audit_events",
        ["action", sa.text("occurred_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_file_audit_action_occurred", table_name="file_audit_events")
    op.drop_index("ix_file_audit_file_occurred", table_name="file_audit_events")
    op.drop_index("ix_file_audit_principal_occurred", table_name="file_audit_events")
    op.drop_table("file_audit_events")
