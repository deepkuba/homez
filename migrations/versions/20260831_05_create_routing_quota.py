"""Add route quota ledger and cached route observations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_05"
down_revision: str | None = "20260831_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "routing_quota_ledger",
        sa.Column("period", sa.String(7), primary_key=True),
        sa.Column("provider", sa.String(100), primary_key=True),
        sa.Column("billable_unit", sa.String(50), primary_key=True),
        sa.Column("allowance", sa.Integer(), nullable=False),
        sa.Column("reserved_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "provider_blocked", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("last_alert_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "route_observations",
        sa.Column("cache_key", sa.String(500), primary_key=True),
        sa.Column("goal_version", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("route_observations")
    op.drop_table("routing_quota_ledger")
