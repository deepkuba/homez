"""Allow ISO calendar dates as report delivery periods."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_20"
down_revision: str | None = "20260907_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("report_drafts") as batch_op:
        batch_op.alter_column(
            "period", existing_type=sa.String(8), type_=sa.String(10), nullable=False
        )
    with op.batch_alter_table("digest_deliveries") as batch_op:
        batch_op.alter_column(
            "period", existing_type=sa.String(8), type_=sa.String(10), nullable=False
        )


def downgrade() -> None:
    op.execute("DELETE FROM digest_deliveries WHERE length(period) > 8")
    op.execute(
        "DELETE FROM report_items WHERE report_id IN "
        "(SELECT id FROM report_drafts WHERE length(period) > 8)"
    )
    op.execute("DELETE FROM report_drafts WHERE length(period) > 8")
    with op.batch_alter_table("digest_deliveries") as batch_op:
        batch_op.alter_column(
            "period", existing_type=sa.String(10), type_=sa.String(8), nullable=False
        )
    with op.batch_alter_table("report_drafts") as batch_op:
        batch_op.alter_column(
            "period", existing_type=sa.String(10), type_=sa.String(8), nullable=False
        )
