"""Principal aggregate models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_smith.infra.storage.postgres.database import Base

if TYPE_CHECKING:
    from agent_smith.infra.storage.postgres.models.identity_providers import ExternalIdentity


class PrincipalStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    pending = "pending"


class Principal(Base):
    __tablename__ = "principals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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

    external_identities: Mapped[list[ExternalIdentity]] = relationship(back_populates="principal")
