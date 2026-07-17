"""Bind immutable managed files to session entries.

Revision ID: 011_session_entry_files
Revises: 010_managed_files
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "011_session_entry_files"
down_revision: Union[str, None] = "010_managed_files"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("object_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "session_entry_files",
        sa.Column(
            "session_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("session_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("session_entry_id", "position"),
        sa.UniqueConstraint(
            "session_entry_id",
            "file_id",
            "purpose",
            name="uq_session_entry_files_file_purpose",
        ),
    )
    op.create_index(
        "ix_session_entry_files_entry", "session_entry_files", ["session_entry_id"]
    )
    op.create_index("ix_session_entry_files_file", "session_entry_files", ["file_id"])


def downgrade() -> None:
    op.drop_index("ix_session_entry_files_file", table_name="session_entry_files")
    op.drop_index("ix_session_entry_files_entry", table_name="session_entry_files")
    op.drop_table("session_entry_files")
    op.drop_column("files", "object_deleted_at")
