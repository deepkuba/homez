"""Persist complete route requests, quota ceilings, and pending work."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_13"
down_revision: str | None = "20260901_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "routing_quota_ledger",
        sa.Column("safety_ceiling", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE routing_quota_ledger SET safety_ceiling = allowance")
    for name, column in (
        (
            "origin",
            sa.Column("origin", sa.String(1000), nullable=False, server_default="[]"),
        ),
        (
            "destination",
            sa.Column(
                "destination", sa.String(1000), nullable=False, server_default="[]"
            ),
        ),
        (
            "requested_at",
            sa.Column(
                "requested_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        ),
        (
            "time_semantics",
            sa.Column(
                "time_semantics",
                sa.String(20),
                nullable=False,
                server_default="departure",
            ),
        ),
        (
            "advisories",
            sa.Column("advisories", sa.Text(), nullable=False, server_default="[]"),
        ),
    ):
        del name
        op.add_column("route_observations", column)
    op.create_table(
        "pending_route_queries",
        sa.Column("cache_key", sa.String(500), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.PrimaryKeyConstraint("cache_key"),
    )
    op.create_index(
        "ix_pending_route_queries_provider", "pending_route_queries", ["provider"]
    )
    op.create_index(
        "ix_pending_route_queries_queued_at", "pending_route_queries", ["queued_at"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pending_route_queries_queued_at", table_name="pending_route_queries"
    )
    op.drop_index(
        "ix_pending_route_queries_provider", table_name="pending_route_queries"
    )
    op.drop_table("pending_route_queries")
    with op.batch_alter_table("route_observations") as batch_op:
        batch_op.drop_column("advisories")
        batch_op.drop_column("time_semantics")
        batch_op.drop_column("requested_at")
        batch_op.drop_column("destination")
        batch_op.drop_column("origin")
    with op.batch_alter_table("routing_quota_ledger") as batch_op:
        batch_op.drop_column("safety_ceiling")
