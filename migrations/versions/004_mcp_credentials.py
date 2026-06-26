"""Add MCP credential table."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_mcp_credentials"
down_revision: Union[str, None] = "003_session_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_key", sa.String(length=512), nullable=False),
        sa.Column("server_name", sa.String(length=255), nullable=False),
        sa.Column("auth_ref_key", sa.String(length=512), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column(
            "encryption_scheme",
            sa.String(length=64),
            nullable=False,
            server_default="fernet:v1",
        ),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint(
            "principal_key",
            "server_name",
            "auth_ref_key",
            name="uq_mcp_credentials_principal_server_auth_ref",
        ),
    )
    op.create_index("ix_mcp_credentials_server_name", "mcp_credentials", ["server_name"])
    op.create_index(
        "ix_mcp_credentials_principal_server",
        "mcp_credentials",
        ["principal_key", "server_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_credentials_principal_server", table_name="mcp_credentials")
    op.drop_index("ix_mcp_credentials_server_name", table_name="mcp_credentials")
    op.drop_table("mcp_credentials")
