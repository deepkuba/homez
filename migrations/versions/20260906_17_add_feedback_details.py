"""Add structured dislike reasons and optional feedback comments."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_17"
down_revision: str | None = "20260901_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("feedback_events") as batch_op:
        batch_op.add_column(sa.Column("reason_code", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("comment", sa.String(500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("feedback_events") as batch_op:
        batch_op.drop_column("comment")
        batch_op.drop_column("reason_code")
