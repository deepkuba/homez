"""Persist versioned, approval-gated buyer profiles."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_10"
down_revision: str | None = "20260831_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "buyer_profiles",
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.String(length=10), nullable=False),
        sa.Column("profile_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "(approved_at IS NULL AND approved_by IS NULL) OR "
            "(approved_at IS NOT NULL AND approved_by IS NOT NULL)",
            name="ck_buyer_profiles_approval_complete",
        ),
        sa.PrimaryKeyConstraint("version"),
    )


def downgrade() -> None:
    op.drop_table("buyer_profiles")
