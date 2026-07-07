"""Add app assertion nonces and identity metadata."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008_app_assertions_identity_metadata"
down_revision: Union[str, None] = "007_simplify_principal_and_session_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("external_identities", sa.Column("display_name", sa.String(length=255), nullable=True))
    op.add_column("external_identities", sa.Column("source_issuer", sa.String(length=128), nullable=True))
    op.add_column(
        "external_identities",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "external_identities",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "external_identities",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "app_assertion_nonces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("issuer", sa.String(length=128), nullable=False),
        sa.Column("jti", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("issuer", "jti", name="uq_app_assertion_nonces_issuer_jti"),
    )
    op.create_index("ix_app_assertion_nonces_expires_at", "app_assertion_nonces", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_app_assertion_nonces_expires_at", table_name="app_assertion_nonces")
    op.drop_table("app_assertion_nonces")
    op.drop_column("external_identities", "updated_at")
    op.drop_column("external_identities", "last_seen_at")
    op.drop_column("external_identities", "metadata")
    op.drop_column("external_identities", "source_issuer")
    op.drop_column("external_identities", "display_name")
