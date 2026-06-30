"""Add session provenance fields."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_session_provenance"
down_revision: Union[str, None] = "002_resource_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    session_kind = postgresql.ENUM(
        "main",
        "sub_agent",
        name="session_kind",
        create_type=False,
    )
    session_kind.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "sessions",
        sa.Column(
            "kind",
            session_kind,
            nullable=False,
            server_default=sa.text("'main'"),
        ),
    )
    op.add_column(
        "sessions",
        sa.Column("parent_session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("sessions", sa.Column("agent_name", sa.String(length=255), nullable=True))
    op.add_column("sessions", sa.Column("origin_task_id", sa.String(length=255), nullable=True))
    op.add_column(
        "sessions",
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_foreign_key(
        "fk_sessions_parent_session_id_sessions",
        "sessions",
        "sessions",
        ["parent_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_sessions_parent_session_id", "sessions", ["parent_session_id"])
    op.create_index("ix_sessions_origin_task_id", "sessions", ["origin_task_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_origin_task_id", table_name="sessions")
    op.drop_index("ix_sessions_parent_session_id", table_name="sessions")
    op.drop_constraint(
        "fk_sessions_parent_session_id_sessions",
        "sessions",
        type_="foreignkey",
    )
    op.drop_column("sessions", "provenance")
    op.drop_column("sessions", "origin_task_id")
    op.drop_column("sessions", "agent_name")
    op.drop_column("sessions", "parent_session_id")
    op.drop_column("sessions", "kind")
    sa.Enum(name="session_kind").drop(op.get_bind(), checkfirst=True)
