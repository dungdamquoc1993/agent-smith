"""Add agent run and LLM call persistence.

Revision ID: 015_agent_runs_and_llm_calls
Revises: 014_admin_control_plane_foundation
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "015_agent_runs_and_llm_calls"
down_revision: Union[str, None] = "014_admin_control_plane_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    agent_run_status = postgresql.ENUM(
        "running",
        "completed",
        "failed",
        "aborted",
        name="agent_run_status",
        create_type=False,
    )
    llm_call_status = postgresql.ENUM(
        "started",
        "succeeded",
        "failed",
        "aborted",
        name="llm_call_status",
        create_type=False,
    )
    agent_run_recording_status = postgresql.ENUM(
        "pending",
        "complete",
        "degraded",
        name="agent_run_recording_status",
        create_type=False,
    )
    agent_run_status.create(op.get_bind(), checkfirst=True)
    llm_call_status.create(op.get_bind(), checkfirst=True)
    agent_run_recording_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("agent_name", sa.String(255), nullable=True),
        sa.Column("flow", sa.String(100), nullable=False),
        sa.Column(
            "status", agent_run_status, server_default=sa.text("'running'"), nullable=False
        ),
        sa.Column(
            "recording_status",
            agent_run_recording_status,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column("trace_id", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_agent_runs_completed_after_started",
        ),
    )
    op.create_index("ix_agent_runs_session_started", "agent_runs", ["session_id", "started_at"])
    op.create_index("ix_agent_runs_parent_run_id", "agent_runs", ["parent_run_id"])
    op.create_index(
        "ix_agent_runs_principal_started", "agent_runs", ["principal_id", "started_at"]
    )
    op.create_index("ix_agent_runs_status_started", "agent_runs", ["status", "started_at"])
    op.create_index("ix_agent_runs_correlation_id", "agent_runs", ["correlation_id"])

    op.create_table(
        "llm_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("session_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(100), nullable=False),
        sa.Column("api", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("requested_model", sa.String(255), nullable=False),
        sa.Column("response_model", sa.String(255), nullable=True),
        sa.Column("provider_response_id", sa.String(512), nullable=True),
        sa.Column(
            "status", llm_call_status, server_default=sa.text("'started'"), nullable=False
        ),
        sa.Column("stop_reason", sa.String(50), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("first_token_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("time_to_first_token_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("cache_write_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("input_cost", sa.Numeric(20, 10), server_default="0", nullable=False),
        sa.Column("output_cost", sa.Numeric(20, 10), server_default="0", nullable=False),
        sa.Column("cache_read_cost", sa.Numeric(20, 10), server_default="0", nullable=False),
        sa.Column("cache_write_cost", sa.Numeric(20, 10), server_default="0", nullable=False),
        sa.Column("total_cost", sa.Numeric(20, 10), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("error_type", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.UniqueConstraint("run_id", "sequence", name="uq_llm_calls_run_sequence"),
        sa.UniqueConstraint("session_entry_id", name="uq_llm_calls_session_entry"),
        sa.CheckConstraint("sequence >= 1", name="ck_llm_calls_sequence"),
        sa.CheckConstraint("attempt_count >= 1", name="ck_llm_calls_attempt_count"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_llm_calls_duration"
        ),
        sa.CheckConstraint(
            "time_to_first_token_ms IS NULL OR time_to_first_token_ms >= 0",
            name="ck_llm_calls_time_to_first_token",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 "
            "AND cache_read_tokens >= 0 AND cache_write_tokens >= 0 "
            "AND total_tokens >= 0",
            name="ck_llm_calls_token_counts",
        ),
        sa.CheckConstraint(
            "input_cost >= 0 AND output_cost >= 0 "
            "AND cache_read_cost >= 0 AND cache_write_cost >= 0 "
            "AND total_cost >= 0",
            name="ck_llm_calls_costs",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_llm_calls_completed_after_started",
        ),
    )
    op.create_index("ix_llm_calls_session_started", "llm_calls", ["session_id", "started_at"])
    op.create_index(
        "ix_llm_calls_principal_started", "llm_calls", ["principal_id", "started_at"]
    )
    op.create_index(
        "ix_llm_calls_provider_model_started",
        "llm_calls",
        ["provider", "requested_model", "started_at"],
    )
    op.create_index("ix_llm_calls_status_started", "llm_calls", ["status", "started_at"])
    op.create_index(
        "ix_llm_calls_provider_response_id", "llm_calls", ["provider_response_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_llm_calls_provider_response_id", table_name="llm_calls")
    op.drop_index("ix_llm_calls_status_started", table_name="llm_calls")
    op.drop_index("ix_llm_calls_provider_model_started", table_name="llm_calls")
    op.drop_index("ix_llm_calls_principal_started", table_name="llm_calls")
    op.drop_index("ix_llm_calls_session_started", table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index("ix_agent_runs_correlation_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_status_started", table_name="agent_runs")
    op.drop_index("ix_agent_runs_principal_started", table_name="agent_runs")
    op.drop_index("ix_agent_runs_session_started", table_name="agent_runs")
    op.drop_index("ix_agent_runs_parent_run_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    postgresql.ENUM(name="llm_call_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="agent_run_recording_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="agent_run_status").drop(op.get_bind(), checkfirst=True)
