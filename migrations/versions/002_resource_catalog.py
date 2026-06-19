"""Add resource catalog tables."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_resource_catalog"
down_revision: Union[str, None] = "001_initial_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    resource_kind = sa.Enum(
        "skill",
        "prompt_template",
        "agent_definition",
        "mcp_server_config",
        name="resource_kind",
    )
    resource_scope = sa.Enum(
        "builtin",
        "file",
        "project",
        "user",
        "session",
        name="resource_scope",
    )
    resource_source_type = sa.Enum(
        "builtin",
        "filesystem",
        "memory",
        "plugin",
        "postgres",
        name="resource_source_type",
    )

    resource_kind.create(op.get_bind(), checkfirst=True)
    resource_scope.create(op.get_bind(), checkfirst=True)
    resource_source_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "resources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", resource_kind, nullable=False),
        sa.Column("scope", resource_scope, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_type", resource_source_type, nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=True),
        sa.Column("source_uri", sa.String(length=2048), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("kind", "scope", "name", name="uq_resources_kind_scope_name"),
    )
    op.create_index("ix_resources_kind_name", "resources", ["kind", "name"])
    op.create_index("ix_resources_kind_scope_name", "resources", ["kind", "scope", "name"])

    op.create_table(
        "resource_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "resource_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("resource_id", "version", name="uq_resource_versions_resource_version"),
    )
    op.create_index(
        "ix_resource_versions_resource_version",
        "resource_versions",
        ["resource_id", "version"],
    )


def downgrade() -> None:
    op.drop_index("ix_resource_versions_resource_version", table_name="resource_versions")
    op.drop_table("resource_versions")
    op.drop_index("ix_resources_kind_scope_name", table_name="resources")
    op.drop_index("ix_resources_kind_name", table_name="resources")
    op.drop_table("resources")

    sa.Enum(name="resource_source_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="resource_scope").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="resource_kind").drop(op.get_bind(), checkfirst=True)
