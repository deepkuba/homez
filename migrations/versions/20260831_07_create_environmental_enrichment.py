"""Add dated environmental evidence and manual corrections."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_07"
down_revision: str | None = "20260831_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "environmental_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("property_id", sa.String(100), nullable=False),
        sa.Column("field", sa.String(100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.UniqueConstraint("property_id", "field", "observed_at"),
    )
    op.create_index(
        "ix_environmental_evidence_property_id",
        "environmental_evidence",
        ["property_id"],
    )
    op.create_table(
        "environmental_corrections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("property_id", sa.String(100), nullable=False),
        sa.Column("field", sa.String(100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("corrected_by", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_environmental_corrections_property_id",
        "environmental_corrections",
        ["property_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_environmental_corrections_property_id",
        table_name="environmental_corrections",
    )
    op.drop_table("environmental_corrections")
    op.drop_index(
        "ix_environmental_evidence_property_id", table_name="environmental_evidence"
    )
    op.drop_table("environmental_evidence")
