"""Persist explicit production field outcomes and provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_27"
down_revision: str | None = "20260910_26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_resolved_fields",
        sa.Column(
            "result_id",
            sa.Uuid(),
            sa.ForeignKey("production_parser_results.id"),
            primary_key=True,
        ),
        sa.Column("name", sa.String(50), primary_key=True),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=True),
        sa.Column("selected_origin", sa.String(80), nullable=True),
        sa.CheckConstraint(
            "state IN ('value', 'unknown', 'ambiguous')",
            name="ck_production_resolved_field_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("production_resolved_fields")
