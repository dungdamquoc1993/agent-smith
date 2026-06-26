"""Drop local credential table."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005_drop_local_credentials"
down_revision: Union[str, None] = "004_mcp_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("local_credentials")


def downgrade() -> None:
    op.create_table(
        "local_credentials",
        sa.Column(
            "external_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("external_identities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
