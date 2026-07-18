"""Admin control-plane operator, session, and immutable audit models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agent_smith.infra.storage.postgres.database import Base


class AdminOperatorStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"


class AdminOperator(Base):
    __tablename__ = "admin_operators"
    __table_args__ = (
        CheckConstraint(
            "username = lower(btrim(username))",
            name="ck_admin_operators_username_normalized",
        ),
        CheckConstraint(
            "failed_login_count >= 0", name="ck_admin_operators_failed_login_count"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AdminOperatorStatus] = mapped_column(
        Enum(AdminOperatorStatus, name="admin_operator_status"),
        nullable=False,
        default=AdminOperatorStatus.active,
        server_default=AdminOperatorStatus.active.value,
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = (
        CheckConstraint(
            "char_length(token_hash) = 64", name="ck_admin_sessions_token_hash_length"
        ),
        CheckConstraint(
            "char_length(csrf_token_hash) = 64",
            name="ck_admin_sessions_csrf_token_hash_length",
        ),
        Index(
            "ix_admin_sessions_operator_revoked_absolute",
            "operator_id",
            "revoked_at",
            "absolute_expires_at",
        ),
        Index("ix_admin_sessions_idle_expires_at", "idle_expires_at"),
        Index("ix_admin_sessions_absolute_expires_at", "absolute_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admin_operators.id", ondelete="RESTRICT"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"
    __table_args__ = (
        Index("ix_admin_audit_occurred_at", "occurred_at"),
        Index("ix_admin_audit_actor_occurred", "actor_operator_id", "occurred_at"),
        Index("ix_admin_audit_action_occurred", "action", "occurred_at"),
        Index(
            "ix_admin_audit_sign_in_username",
            "action",
            "outcome",
            "resource_id",
            "occurred_at",
        ),
        Index(
            "ix_admin_audit_sign_in_ip",
            "action",
            "outcome",
            "ip_address",
            "occurred_at",
        ),
        Index(
            "ix_admin_audit_resource_occurred",
            "resource_type",
            "resource_id",
            "occurred_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admin_operators.id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
