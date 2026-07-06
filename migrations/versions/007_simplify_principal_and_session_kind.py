"""Simplify principals and session kind values."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007_simplify_principal_and_session_kind"
down_revision: Union[str, None] = "006_user_memory_resource"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("ALTER TABLE sessions ALTER COLUMN kind DROP DEFAULT")
    op.execute("ALTER TYPE session_kind RENAME TO session_kind_old")
    session_kind = postgresql.ENUM("chat", "agent_run", name="session_kind")
    session_kind.create(bind, checkfirst=True)
    op.execute(
        """
        ALTER TABLE sessions
        ALTER COLUMN kind TYPE session_kind
        USING (
            CASE kind::text
                WHEN 'main' THEN 'chat'
                WHEN 'sub_agent' THEN 'agent_run'
                ELSE kind::text
            END
        )::session_kind
        """
    )
    op.execute("ALTER TABLE sessions ALTER COLUMN kind SET DEFAULT 'chat'")
    op.execute("DROP TYPE session_kind_old")

    op.drop_column("principals", "type")
    op.execute("DROP TYPE IF EXISTS principal_type")


def downgrade() -> None:
    bind = op.get_bind()

    principal_type = postgresql.ENUM(
        "human",
        "service_account",
        "agent",
        "subagent",
        "system_job",
        name="principal_type",
    )
    principal_type.create(bind, checkfirst=True)
    op.add_column(
        "principals",
        sa.Column(
            "type",
            principal_type,
            nullable=False,
            server_default=sa.text("'human'"),
        ),
    )
    op.alter_column("principals", "type", server_default=None)

    op.execute("ALTER TABLE sessions ALTER COLUMN kind DROP DEFAULT")
    op.execute("ALTER TYPE session_kind RENAME TO session_kind_new")
    session_kind = postgresql.ENUM("main", "sub_agent", name="session_kind")
    session_kind.create(bind, checkfirst=True)
    op.execute(
        """
        ALTER TABLE sessions
        ALTER COLUMN kind TYPE session_kind
        USING (
            CASE kind::text
                WHEN 'chat' THEN 'main'
                WHEN 'agent_run' THEN 'sub_agent'
                ELSE kind::text
            END
        )::session_kind
        """
    )
    op.execute("ALTER TABLE sessions ALTER COLUMN kind SET DEFAULT 'main'")
    op.execute("DROP TYPE session_kind_new")
