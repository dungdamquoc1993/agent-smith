"""Add identity provider registry and scoped external identities."""

from __future__ import annotations

import re
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "009_identity_provider_registry"
down_revision: Union[str, None] = "008_app_assertions_identity_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PROVIDER_STATUS = ("active", "inactive", "pending")
KEY_STATUS = ("active", "revoked", "expired")


def upgrade() -> None:
    bind = op.get_bind()
    provider_status = postgresql.ENUM(
        *PROVIDER_STATUS,
        name="identity_provider_status",
        create_type=False,
    )
    key_status = postgresql.ENUM(
        *KEY_STATUS,
        name="identity_provider_key_status",
        create_type=False,
    )
    provider_status.create(bind, checkfirst=True)
    key_status.create(bind, checkfirst=True)

    op.create_table(
        "identity_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("issuer", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            provider_status,
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("slug", name="uq_identity_providers_slug"),
        sa.UniqueConstraint("issuer", name="uq_identity_providers_issuer"),
    )

    op.create_table(
        "identity_provider_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identity_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("status", key_status, server_default=sa.text("'active'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("key_hash", name="uq_identity_provider_api_keys_key_hash"),
    )
    op.create_index(
        "ix_identity_provider_api_keys_provider",
        "identity_provider_api_keys",
        ["provider_id"],
    )
    op.create_index(
        "ix_identity_provider_api_keys_key_prefix",
        "identity_provider_api_keys",
        ["key_prefix"],
    )

    op.create_table(
        "identity_provider_assertion_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identity_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kid", sa.String(length=128), nullable=False),
        sa.Column("alg", sa.String(length=32), server_default="HS256", nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("encryption_scheme", sa.String(length=64), server_default="fernet:v1", nullable=False),
        sa.Column("status", key_status, server_default=sa.text("'active'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint(
            "provider_id",
            "kid",
            name="uq_identity_provider_assertion_keys_provider_kid",
        ),
    )
    op.create_index(
        "ix_identity_provider_assertion_keys_provider",
        "identity_provider_assertion_keys",
        ["provider_id"],
    )

    op.add_column(
        "external_identities",
        sa.Column(
            "identity_provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identity_providers.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    if not context.is_offline_mode():
        _backfill_identity_providers()
    else:
        op.execute("-- data backfill for identity providers requires online migration")
    op.alter_column("external_identities", "identity_provider_id", nullable=False)
    op.create_index(
        "ix_external_identities_identity_provider",
        "external_identities",
        ["identity_provider_id"],
    )
    op.drop_constraint(
        "uq_external_identity_provider_subject",
        "external_identities",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_external_identity_identity_provider_subject",
        "external_identities",
        ["identity_provider_id", "subject"],
    )
    op.drop_column("external_identities", "source_issuer")
    op.drop_column("external_identities", "provider")


def downgrade() -> None:
    op.add_column("external_identities", sa.Column("provider", sa.String(length=128), nullable=True))
    op.add_column("external_identities", sa.Column("source_issuer", sa.String(length=128), nullable=True))
    if not context.is_offline_mode():
        op.execute(
            """
            UPDATE external_identities AS external
            SET
                provider = providers.slug,
                source_issuer = providers.issuer
            FROM identity_providers AS providers
            WHERE external.identity_provider_id = providers.id
            """
        )
    else:
        op.execute("-- data backfill for legacy external identity fields requires online migration")
    op.alter_column("external_identities", "provider", nullable=False)
    op.drop_constraint(
        "uq_external_identity_identity_provider_subject",
        "external_identities",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_external_identity_provider_subject",
        "external_identities",
        ["provider", "subject"],
    )
    op.drop_index("ix_external_identities_identity_provider", table_name="external_identities")
    op.drop_column("external_identities", "identity_provider_id")

    op.drop_index(
        "ix_identity_provider_assertion_keys_provider",
        table_name="identity_provider_assertion_keys",
    )
    op.drop_table("identity_provider_assertion_keys")
    op.drop_index("ix_identity_provider_api_keys_key_prefix", table_name="identity_provider_api_keys")
    op.drop_index("ix_identity_provider_api_keys_provider", table_name="identity_provider_api_keys")
    op.drop_table("identity_provider_api_keys")
    op.drop_table("identity_providers")
    op.execute("DROP TYPE IF EXISTS identity_provider_key_status")
    op.execute("DROP TYPE IF EXISTS identity_provider_status")


def _backfill_identity_providers() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT COALESCE(source_issuer, provider) AS namespace
            FROM external_identities
            WHERE COALESCE(source_issuer, provider) IS NOT NULL
            ORDER BY namespace
            """
        )
    ).mappings()
    provider_ids: dict[str, uuid.UUID] = {}
    used_slugs: set[str] = set()
    for row in rows:
        namespace = str(row["namespace"])
        provider_id = uuid.uuid4()
        slug = _unique_slug(namespace, used_slugs)
        provider_ids[namespace] = provider_id
        connection.execute(
            sa.text(
                """
                INSERT INTO identity_providers
                    (id, slug, issuer, display_name, status, metadata)
                VALUES
                    (:id, :slug, :issuer, :display_name, 'active', '{}'::jsonb)
                """
            ),
            {
                "id": provider_id,
                "slug": slug,
                "issuer": namespace[:128],
                "display_name": namespace[:255],
            },
        )

    for namespace, provider_id in provider_ids.items():
        connection.execute(
            sa.text(
                """
                UPDATE external_identities
                SET identity_provider_id = :provider_id
                WHERE COALESCE(source_issuer, provider) = :namespace
                """
            ),
            {"provider_id": provider_id, "namespace": namespace},
        )


def _unique_slug(value: str, used: set[str]) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-_")
    if not base:
        base = "provider"
    base = base[:118]
    slug = base
    index = 2
    while slug in used:
        suffix = f"-{index}"
        slug = f"{base[: 128 - len(suffix)]}{suffix}"
        index += 1
    used.add(slug)
    return slug
