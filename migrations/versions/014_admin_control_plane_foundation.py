"""Add admin operator, session, and audit persistence.

Revision ID: 014_admin_control_plane_foundation
Revises: 013_file_storage_hardening
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "014_admin_control_plane_foundation"
down_revision: Union[str, None] = "013_file_storage_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    operator_status = postgresql.ENUM(
        "active", "disabled", name="admin_operator_status", create_type=False
    )
    operator_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "admin_operators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(128), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "status",
            operator_status,
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("failed_login_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "failed_login_count >= 0", name="ck_admin_operators_failed_login_count"
        ),
        sa.CheckConstraint(
            "username = lower(btrim(username))",
            name="ck_admin_operators_username_normalized",
        ),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "operator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_operators.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.CheckConstraint(
            "char_length(token_hash) = 64", name="ck_admin_sessions_token_hash_length"
        ),
        sa.CheckConstraint(
            "char_length(csrf_token_hash) = 64",
            name="ck_admin_sessions_csrf_token_hash_length",
        ),
    )
    op.create_index(
        "ix_admin_sessions_operator_revoked_absolute",
        "admin_sessions",
        ["operator_id", "revoked_at", "absolute_expires_at"],
    )
    op.create_index(
        "ix_admin_sessions_idle_expires_at", "admin_sessions", ["idle_expires_at"]
    )
    op.create_index(
        "ix_admin_sessions_absolute_expires_at", "admin_sessions", ["absolute_expires_at"]
    )
    op.create_table(
        "admin_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_kind", sa.String(64), nullable=False),
        sa.Column(
            "actor_operator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_operators.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("actor_identifier", sa.String(255), nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=True),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_audit_occurred_at", "admin_audit_events", ["occurred_at"])
    op.create_index(
        "ix_admin_audit_actor_occurred",
        "admin_audit_events",
        ["actor_operator_id", "occurred_at"],
    )
    op.create_index(
        "ix_admin_audit_action_occurred", "admin_audit_events", ["action", "occurred_at"]
    )
    op.create_index(
        "ix_admin_audit_sign_in_username",
        "admin_audit_events",
        ["action", "outcome", "resource_id", "occurred_at"],
    )
    op.create_index(
        "ix_admin_audit_sign_in_ip",
        "admin_audit_events",
        ["action", "outcome", "ip_address", "occurred_at"],
    )
    op.create_index(
        "ix_admin_audit_resource_occurred",
        "admin_audit_events",
        ["resource_type", "resource_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_admin_audit_resource_occurred", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_action_occurred", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_sign_in_ip", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_sign_in_username", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_actor_occurred", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_occurred_at", table_name="admin_audit_events")
    op.drop_table("admin_audit_events")
    op.drop_index("ix_admin_sessions_absolute_expires_at", table_name="admin_sessions")
    op.drop_index("ix_admin_sessions_idle_expires_at", table_name="admin_sessions")
    op.drop_index("ix_admin_sessions_operator_revoked_absolute", table_name="admin_sessions")
    op.drop_table("admin_sessions")
    op.drop_table("admin_operators")
    postgresql.ENUM(name="admin_operator_status").drop(op.get_bind(), checkfirst=True)
