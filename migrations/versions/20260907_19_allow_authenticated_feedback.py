"""Allow authenticated offer-browser feedback without capability tokens."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_19"
down_revision: str | None = "20260906_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("feedback_events") as batch_op:
        batch_op.alter_column("token_hash", existing_type=sa.String(64), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM feedback_events WHERE token_hash IS NULL")
    with op.batch_alter_table("feedback_events") as batch_op:
        batch_op.alter_column("token_hash", existing_type=sa.String(64), nullable=False)
