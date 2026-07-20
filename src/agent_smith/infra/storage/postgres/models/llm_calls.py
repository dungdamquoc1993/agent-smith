"""Agent-run and LLM-call persistence models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_smith.infra.storage.postgres.database import Base


class AgentRunStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    aborted = "aborted"


class AgentRunRecordingStatus(str, enum.Enum):
    pending = "pending"
    complete = "complete"
    degraded = "degraded"


class LlmCallStatus(str, enum.Enum):
    started = "started"
    succeeded = "succeeded"
    failed = "failed"
    aborted = "aborted"


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_session_started", "session_id", "started_at"),
        Index("ix_agent_runs_parent_run_id", "parent_run_id"),
        Index("ix_agent_runs_principal_started", "principal_id", "started_at"),
        Index("ix_agent_runs_status_started", "status", "started_at"),
        Index("ix_agent_runs_correlation_id", "correlation_id"),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_agent_runs_completed_after_started",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    agent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    flow: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, name="agent_run_status"),
        nullable=False,
        default=AgentRunStatus.running,
        server_default=AgentRunStatus.running.value,
    )
    recording_status: Mapped[AgentRunRecordingStatus] = mapped_column(
        Enum(AgentRunRecordingStatus, name="agent_run_recording_status"),
        nullable=False,
        default=AgentRunRecordingStatus.pending,
        server_default=AgentRunRecordingStatus.pending.value,
    )
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    calls: Mapped[list["LlmCall"]] = relationship(back_populates="run")


class LlmCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_llm_calls_run_sequence"),
        UniqueConstraint("session_entry_id", name="uq_llm_calls_session_entry"),
        Index("ix_llm_calls_session_started", "session_id", "started_at"),
        Index("ix_llm_calls_principal_started", "principal_id", "started_at"),
        Index(
            "ix_llm_calls_provider_model_started",
            "provider",
            "requested_model",
            "started_at",
        ),
        Index("ix_llm_calls_status_started", "status", "started_at"),
        Index("ix_llm_calls_provider_response_id", "provider_response_id"),
        CheckConstraint("sequence >= 1", name="ck_llm_calls_sequence"),
        CheckConstraint("attempt_count >= 1", name="ck_llm_calls_attempt_count"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_llm_calls_duration"),
        CheckConstraint(
            "time_to_first_token_ms IS NULL OR time_to_first_token_ms >= 0",
            name="ck_llm_calls_time_to_first_token",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 "
            "AND cache_read_tokens >= 0 AND cache_write_tokens >= 0 "
            "AND total_tokens >= 0",
            name="ck_llm_calls_token_counts",
        ),
        CheckConstraint(
            "input_cost >= 0 AND output_cost >= 0 "
            "AND cache_read_cost >= 0 AND cache_write_cost >= 0 "
            "AND total_cost >= 0",
            name="ck_llm_calls_costs",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_llm_calls_completed_after_started",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    session_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(100), nullable=False)
    api: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(255), nullable=False)
    response_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_response_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[LlmCallStatus] = mapped_column(
        Enum(LlmCallStatus, name="llm_call_status"),
        nullable=False,
        default=LlmCallStatus.started,
        server_default=LlmCallStatus.started.value,
    )
    stop_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    first_token_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_to_first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    cache_write_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    input_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal("0"), server_default="0"
    )
    output_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal("0"), server_default="0"
    )
    cache_read_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal("0"), server_default="0"
    )
    cache_write_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal("0"), server_default="0"
    )
    total_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal("0"), server_default="0"
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    error_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    call_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    run: Mapped[AgentRun | None] = relationship(back_populates="calls")
