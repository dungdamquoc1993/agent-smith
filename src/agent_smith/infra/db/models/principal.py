"""Principal and identity models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_smith.infra.db.base import Base


class PrincipalStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    pending = "pending"


class IdentityProviderStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    pending = "pending"


class IdentityProviderKeyStatus(str, enum.Enum):
    active = "active"
    revoked = "revoked"
    expired = "expired"


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

    external_identities: Mapped[list["ExternalIdentity"]] = relationship(back_populates="principal")


class IdentityProvider(Base):
    __tablename__ = "identity_providers"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_identity_providers_slug"),
        UniqueConstraint("issuer", name="uq_identity_providers_issuer"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    issuer: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[IdentityProviderStatus] = mapped_column(
        Enum(IdentityProviderStatus, name="identity_provider_status"),
        nullable=False,
        default=IdentityProviderStatus.active,
        server_default=IdentityProviderStatus.active.value,
    )
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    api_keys: Mapped[list["IdentityProviderApiKey"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
    )
    assertion_keys: Mapped[list["IdentityProviderAssertionKey"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
    )
    external_identities: Mapped[list["ExternalIdentity"]] = relationship(back_populates="identity_provider")


class IdentityProviderApiKey(Base):
    __tablename__ = "identity_provider_api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_identity_provider_api_keys_key_hash"),
        Index("ix_identity_provider_api_keys_provider", "provider_id"),
        Index("ix_identity_provider_api_keys_key_prefix", "key_prefix"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity_providers.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[IdentityProviderKeyStatus] = mapped_column(
        Enum(IdentityProviderKeyStatus, name="identity_provider_key_status"),
        nullable=False,
        default=IdentityProviderKeyStatus.active,
        server_default=IdentityProviderKeyStatus.active.value,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    provider: Mapped[IdentityProvider] = relationship(back_populates="api_keys")


class IdentityProviderAssertionKey(Base):
    __tablename__ = "identity_provider_assertion_keys"
    __table_args__ = (
        UniqueConstraint("provider_id", "kid", name="uq_identity_provider_assertion_keys_provider_kid"),
        Index("ix_identity_provider_assertion_keys_provider", "provider_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity_providers.id", ondelete="CASCADE"), nullable=False
    )
    kid: Mapped[str] = mapped_column(String(128), nullable=False)
    alg: Mapped[str] = mapped_column(String(32), nullable=False, default="HS256", server_default="HS256")
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_scheme: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="fernet:v1",
        server_default="fernet:v1",
    )
    status: Mapped[IdentityProviderKeyStatus] = mapped_column(
        Enum(IdentityProviderKeyStatus, name="identity_provider_key_status"),
        nullable=False,
        default=IdentityProviderKeyStatus.active,
        server_default=IdentityProviderKeyStatus.active.value,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    provider: Mapped[IdentityProvider] = relationship(back_populates="assertion_keys")


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint(
            "identity_provider_id",
            "subject",
            name="uq_external_identity_identity_provider_subject",
        ),
        Index("ix_external_identities_identity_provider", "identity_provider_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    identity_provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity_providers.id", ondelete="RESTRICT"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    identity_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    principal: Mapped[Principal] = relationship(back_populates="external_identities")
    identity_provider: Mapped[IdentityProvider] = relationship(back_populates="external_identities")


class AppAssertionNonce(Base):
    __tablename__ = "app_assertion_nonces"
    __table_args__ = (
        UniqueConstraint("issuer", "jti", name="uq_app_assertion_nonces_issuer_jti"),
        Index("ix_app_assertion_nonces_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    issuer: Mapped[str] = mapped_column(String(128), nullable=False)
    jti: Mapped[str] = mapped_column(String(512), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
