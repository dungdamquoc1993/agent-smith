"""Add user memory resource kind."""

from typing import Sequence, Union

from alembic import op

revision: str = "006_user_memory_resource"
down_revision: Union[str, None] = "005_drop_local_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE resource_kind ADD VALUE IF NOT EXISTS 'user_memory'")


def downgrade() -> None:
    pass
