"""Initial core tables: principals, identities, sessions."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial_core"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    principal_type = sa.Enum(
        "human",
        "service_account",
        "agent",
        "subagent",
        "system_job",
        name="principal_type",
    )
    principal_status = sa.Enum("active", "inactive", "pending", name="principal_status")
    session_entry_type = sa.Enum(
        "message",
        "model_change",
        "thinking_level_change",
        "active_tools_change",
        "compaction",
        "branch_summary",
        "custom",
        "custom_message",
        "label",
        "session_info",
        "leaf",
        name="session_entry_type",
    )

    principal_type.create(op.get_bind(), checkfirst=True)
    principal_status.create(op.get_bind(), checkfirst=True)
    session_entry_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "principals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("type", principal_type, nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("status", principal_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "external_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("provider", "subject", name="uq_external_identity_provider_subject"),
    )

    op.create_table(
        "local_credentials",
        sa.Column("external_identity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("external_identities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("current_leaf_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "session_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("session_entries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("type", session_entry_type, nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("principals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_session_entries_session_parent", "session_entries", ["session_id", "parent_id"])


def downgrade() -> None:
    op.drop_index("ix_session_entries_session_parent", table_name="session_entries")
    op.drop_table("session_entries")
    op.drop_table("sessions")
    op.drop_table("local_credentials")
    op.drop_table("external_identities")
    op.drop_table("principals")

    sa.Enum(name="session_entry_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="principal_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="principal_type").drop(op.get_bind(), checkfirst=True)
