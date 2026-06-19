"""Resource catalog models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class ResourceKindEnum(str, enum.Enum):
    skill = "skill"
    prompt_template = "prompt_template"
    agent_definition = "agent_definition"
    mcp_server_config = "mcp_server_config"


class ResourceScopeEnum(str, enum.Enum):
    builtin = "builtin"
    file = "file"
    project = "project"
    user = "user"
    session = "session"


class ResourceSourceTypeEnum(str, enum.Enum):
    builtin = "builtin"
    filesystem = "filesystem"
    memory = "memory"
    plugin = "plugin"
    postgres = "postgres"


class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("kind", "scope", "name", name="uq_resources_kind_scope_name"),
        Index("ix_resources_kind_name", "kind", "name"),
        Index("ix_resources_kind_scope_name", "kind", "scope", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[ResourceKindEnum] = mapped_column(
        Enum(ResourceKindEnum, name="resource_kind"),
        nullable=False,
    )
    scope: Mapped[ResourceScopeEnum] = mapped_column(
        Enum(ResourceScopeEnum, name="resource_scope"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[ResourceSourceTypeEnum] = mapped_column(
        Enum(ResourceSourceTypeEnum, name="resource_source_type"),
        nullable=False,
        default=ResourceSourceTypeEnum.postgres,
    )
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    disabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    versions: Mapped[list["ResourceVersion"]] = relationship(
        back_populates="resource",
        order_by="ResourceVersion.version",
        cascade="all, delete-orphan",
    )


class ResourceVersion(Base):
    __tablename__ = "resource_versions"
    __table_args__ = (
        UniqueConstraint("resource_id", "version", name="uq_resource_versions_resource_version"),
        Index("ix_resource_versions_resource_version", "resource_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resources.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    resource: Mapped[Resource] = relationship(back_populates="versions")
