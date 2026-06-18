"""Principal and identity models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_smith.db.base import Base


class PrincipalType(str, enum.Enum):
    human = "human"
    service_account = "service_account"
    agent = "agent"
    subagent = "subagent"
    system_job = "system_job"


class PrincipalStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    pending = "pending"


class Principal(Base):
    __tablename__ = "principals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[PrincipalType] = mapped_column(Enum(PrincipalType, name="principal_type"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[PrincipalStatus] = mapped_column(
        Enum(PrincipalStatus, name="principal_status"),
        nullable=False,
        default=PrincipalStatus.active,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    external_identities: Mapped[list["ExternalIdentity"]] = relationship(back_populates="principal")


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_external_identity_provider_subject"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    principal: Mapped[Principal] = relationship(back_populates="external_identities")
    local_credential: Mapped["LocalCredential | None"] = relationship(back_populates="external_identity")


class LocalCredential(Base):
    __tablename__ = "local_credentials"

    external_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("external_identities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    external_identity: Mapped[ExternalIdentity] = relationship(back_populates="local_credential")
