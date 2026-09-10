"""Add safe central metadata for NAS-only parser diagnostics."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_26"
down_revision: str | None = "20260910_25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "result_id",
            sa.Uuid(),
            sa.ForeignKey("production_parser_results.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("artifact_id", sa.String(36), nullable=True),
        sa.Column("artifact_status", sa.String(20), nullable=False),
        sa.Column("missing_fields_json", sa.String(2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "artifact_status IN ('stored', 'unavailable')",
            name="ck_diagnostic_artifact_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("diagnostic_runs")
