"""MCP persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from agent_smith.infra.storage.postgres.database import Base


class McpCredentialRecord(Base):
    __tablename__ = "mcp_credentials"
    __table_args__ = (
        UniqueConstraint(
            "principal_key",
            "server_name",
            "auth_ref_key",
            name="uq_mcp_credentials_principal_server_auth_ref",
        ),
        Index("ix_mcp_credentials_server_name", "server_name"),
        Index("ix_mcp_credentials_principal_server", "principal_key", "server_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    server_name: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_ref_key: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_scheme: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="fernet:v1",
        server_default="fernet:v1",
    )
    disabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
