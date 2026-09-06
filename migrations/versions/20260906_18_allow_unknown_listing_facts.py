"""Allow portal email facts absent from an alert to remain unknown."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_18"
down_revision: str | None = "20260906_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("listing_snapshots") as batch_op:
        batch_op.alter_column("area_sqm", existing_type=sa.Numeric(8, 2), nullable=True)
        batch_op.alter_column("rooms", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column("location", existing_type=sa.String(500), nullable=True)


def downgrade() -> None:
    # Older application versions require concrete values. Preserve rollback
    # ability without inventing plausible facts: these sentinels are visibly
    # invalid and cannot be confused with extracted listing data.
    op.execute("UPDATE listing_snapshots SET area_sqm = 0 WHERE area_sqm IS NULL")
    op.execute("UPDATE listing_snapshots SET rooms = 0 WHERE rooms IS NULL")
    op.execute("UPDATE listing_snapshots SET location = '' WHERE location IS NULL")
    with op.batch_alter_table("listing_snapshots") as batch_op:
        batch_op.alter_column("location", existing_type=sa.String(500), nullable=False)
        batch_op.alter_column("rooms", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column(
            "area_sqm", existing_type=sa.Numeric(8, 2), nullable=False
        )
