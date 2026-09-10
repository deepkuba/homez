"""Add central source pacing, cooldown, and proxy byte ledgers."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_25"
down_revision: str | None = "20260909_24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scrape_tasks",
        sa.Column(
            "direct_fallback_pending",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "scrape_tasks",
        sa.Column(
            "direct_fallback_available_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    with op.batch_alter_table("scrape_attempts") as batch:
        batch.alter_column(
            "route_id",
            existing_type=sa.Uuid(),
            type_=sa.String(64),
            postgresql_using="route_id::text",
        )
    op.add_column(
        "scrape_attempts",
        sa.Column(
            "proxy_reserved_bytes", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "scrape_attempts",
        sa.Column(
            "network_attempt_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "scrape_attempts",
        sa.Column("network_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "scrape_attempts", sa.Column("classification", sa.String(40), nullable=True)
    )
    op.create_table(
        "source_runtime_state",
        sa.Column("source", sa.String(20), primary_key=True),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("day_key", sa.String(10), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "direct_denial_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_table(
        "proxy_usage_ledger",
        sa.Column("billing_cycle", sa.String(7), primary_key=True),
        sa.Column(
            "allocated_bytes", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "transferred_bytes", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    op.create_table(
        "proxy_route_health",
        sa.Column("route_id", sa.String(64), primary_key=True),
        sa.Column("healthy", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "proxy_source_quarantine",
        sa.Column(
            "route_id",
            sa.String(64),
            sa.ForeignKey("proxy_route_health.route_id"),
            primary_key=True,
        ),
        sa.Column("source", sa.String(20), primary_key=True),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "redirect_handoffs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_task_id",
            sa.Uuid(),
            sa.ForeignKey("scrape_tasks.id"),
            nullable=False,
        ),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("target_source", sa.String(20), nullable=False),
        sa.Column("target_listing_id", sa.String(255), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_task_id", "target_source", "target_listing_id"),
        sa.CheckConstraint(
            "source != target_source", name="ck_redirect_handoff_cross_source"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'enqueued')", name="ck_redirect_handoff_state"
        ),
    )
    op.create_index(
        "ix_redirect_handoffs_target_source",
        "redirect_handoffs",
        ["target_source"],
    )


def downgrade() -> None:
    op.drop_index("ix_redirect_handoffs_target_source", table_name="redirect_handoffs")
    op.drop_table("redirect_handoffs")
    op.drop_table("proxy_source_quarantine")
    op.drop_table("proxy_route_health")
    op.drop_table("proxy_usage_ledger")
    op.drop_table("source_runtime_state")
    op.drop_column("scrape_attempts", "network_started_at")
    op.drop_column("scrape_attempts", "classification")
    op.drop_column("scrape_attempts", "network_attempt_count")
    op.drop_column("scrape_attempts", "proxy_reserved_bytes")
    with op.batch_alter_table("scrape_attempts") as batch:
        batch.alter_column(
            "route_id",
            existing_type=sa.String(64),
            type_=sa.Uuid(),
            postgresql_using="route_id::uuid",
        )
    op.drop_column("scrape_tasks", "direct_fallback_available_at")
    op.drop_column("scrape_tasks", "direct_fallback_pending")
