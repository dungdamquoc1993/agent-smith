"""Session event-tree models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_smith.infra.storage.postgres.database import Base


class SessionKind(str, enum.Enum):
    chat = "chat"
    agent_run = "agent_run"


class SessionEntryType(str, enum.Enum):
    message = "message"
    model_change = "model_change"
    thinking_level_change = "thinking_level_change"
    active_tools_change = "active_tools_change"
    compaction = "compaction"
    branch_summary = "branch_summary"
    custom = "custom"
    custom_message = "custom_message"
    label = "label"
    session_info = "session_info"
    leaf = "leaf"


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_parent_session_id", "parent_session_id"),
        Index("ix_sessions_origin_task_id", "origin_task_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    kind: Mapped[SessionKind] = mapped_column(
        Enum(SessionKind, name="session_kind"),
        nullable=False,
        default=SessionKind.chat,
        server_default=SessionKind.chat.value,
    )
    parent_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    origin_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    current_leaf_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    entries: Mapped[list["SessionEntry"]] = relationship(back_populates="session")


class SessionEntry(Base):
    __tablename__ = "session_entries"
    __table_args__ = (Index("ix_session_entries_session_parent", "session_id", "parent_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session_entries.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[SessionEntryType] = mapped_column(
        Enum(SessionEntryType, name="session_entry_type"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    principal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[Session] = relationship(back_populates="entries")


class SessionEntryFile(Base):
    __tablename__ = "session_entry_files"
    __table_args__ = (
        UniqueConstraint(
            "session_entry_id", "file_id", "purpose", name="uq_session_entry_files_file_purpose"
        ),
        Index("ix_session_entry_files_entry", "session_entry_id"),
        Index("ix_session_entry_files_file", "file_id"),
    )

    session_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_entries.id", ondelete="CASCADE"),
        primary_key=True,
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
