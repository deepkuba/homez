"""Add production retention, storage alert, and safe tombstone state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_31"
down_revision: str | None = "20260910_30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "listing_retention_tombstones",
        sa.Column("listing_id", sa.Uuid(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("source_listing_id", sa.String(255), nullable=False),
        sa.Column("canonical_url_hash", sa.String(64), nullable=False),
        sa.Column("confirmed_inactive", sa.Boolean(), nullable=False),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "detailed_data_deleted_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "retention_job_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_results", sa.Integer(), nullable=False),
        sa.Column("deleted_benchmark_results", sa.Integer(), nullable=False),
        sa.Column("failed_results", sa.Integer(), nullable=False),
        sa.Column("backlog_count", sa.Integer(), nullable=False),
        sa.Column("oldest_remaining_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("database_size_before", sa.BigInteger()),
        sa.Column("database_size_after", sa.BigInteger()),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("healthy", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(40)),
    )
    op.create_table(
        "database_size_alert_state",
        sa.Column("boundary_bytes", sa.BigInteger(), primary_key=True),
        sa.Column("armed", sa.Boolean(), nullable=False),
        sa.Column("below_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_alerted_at", sa.DateTime(timezone=True)),
        sa.Column("last_measured_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "retention_alert_state",
        sa.Column("key", sa.String(20), primary_key=True),
        sa.Column("failing", sa.Boolean(), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(timezone=True)),
        sa.Column("last_notified_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(40)),
    )
    op.create_table(
        "benchmark_retention_tombstones",
        sa.Column("run_id", sa.String(100), primary_key=True),
        sa.Column("entry_id", sa.String(100), primary_key=True),
        sa.Column("artifact_id", sa.String(36)),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("variant", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("benchmark_retention_tombstones")
    op.drop_table("retention_alert_state")
    op.drop_table("database_size_alert_state")
    op.drop_table("retention_job_runs")
    op.drop_table("listing_retention_tombstones")
