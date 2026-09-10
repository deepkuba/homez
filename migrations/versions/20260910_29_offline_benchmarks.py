"""Add physically separate offline benchmark persistence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_29"
down_revision: str | None = "20260910_28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "benchmark_manifests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "active_release_hash",
            sa.String(64),
            sa.ForeignKey("parser_releases.release_hash"),
            nullable=False,
        ),
        sa.Column(
            "candidate_release_hash",
            sa.String(64),
            sa.ForeignKey("parser_releases.release_hash"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selection_policy", sa.String(80), nullable=False),
        sa.Column("entries_json", sa.Text(), nullable=False),
        sa.Column("strata_json", sa.Text(), nullable=False),
        sa.Column(
            "data_classification",
            sa.String(20),
            nullable=False,
            server_default="non-production",
        ),
        sa.CheckConstraint(
            "data_classification = 'non-production'",
            name="ck_benchmark_manifest_non_production",
        ),
    )
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column(
            "manifest_id",
            sa.String(64),
            sa.ForeignKey("benchmark_manifests.id"),
            nullable=False,
        ),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "candidate_release_hash",
            sa.String(64),
            sa.ForeignKey("parser_releases.release_hash"),
            nullable=False,
        ),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column(
            "unavailable_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "data_classification",
            sa.String(20),
            nullable=False,
            server_default="non-production",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'complete', 'failed')",
            name="ck_benchmark_run_state",
        ),
        sa.CheckConstraint(
            "data_classification = 'non-production'",
            name="ck_benchmark_run_non_production",
        ),
    )
    op.create_table(
        "benchmark_results",
        sa.Column(
            "run_id",
            sa.String(100),
            sa.ForeignKey("benchmark_runs.id"),
            primary_key=True,
        ),
        sa.Column("entry_id", sa.String(100), primary_key=True),
        sa.Column("input_kind", sa.String(12), nullable=False),
        sa.Column("artifact_id", sa.String(36), nullable=True),
        sa.Column("variant", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("active_result_json", sa.Text(), nullable=True),
        sa.Column("candidate_result_json", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "data_classification",
            sa.String(20),
            nullable=False,
            server_default="non-production",
        ),
        sa.CheckConstraint(
            "status IN ('compared', 'benchmark-input-unavailable')",
            name="ck_benchmark_result_status",
        ),
        sa.CheckConstraint(
            "data_classification = 'non-production'",
            name="ck_benchmark_result_non_production",
        ),
    )
    op.create_table(
        "benchmark_difference_reviews",
        sa.Column(
            "run_id",
            sa.String(100),
            sa.ForeignKey("benchmark_runs.id"),
            primary_key=True,
        ),
        sa.Column("signature", sa.String(64), primary_key=True),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("candidate_adds_value", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("artifact_id", sa.String(36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "data_classification",
            sa.String(20),
            nullable=False,
            server_default="non-production",
        ),
        sa.CheckConstraint(
            "state IN ('unreviewed', 'correct', 'incorrect', 'ambiguous')",
            name="ck_benchmark_review_state",
        ),
        sa.CheckConstraint(
            "data_classification = 'non-production'",
            name="ck_benchmark_review_non_production",
        ),
    )
    op.create_table(
        "benchmark_field_candidates",
        sa.Column("run_id", sa.String(100), nullable=False, primary_key=True),
        sa.Column("entry_id", sa.String(100), nullable=False, primary_key=True),
        sa.Column("parser_side", sa.String(12), nullable=False, primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(80), nullable=False),
        sa.Column("locator", sa.String(200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "data_classification",
            sa.String(20),
            nullable=False,
            server_default="non-production",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "entry_id"],
            ["benchmark_results.run_id", "benchmark_results.entry_id"],
        ),
        sa.CheckConstraint(
            "data_classification = 'non-production'",
            name="ck_benchmark_candidate_non_production",
        ),
    )


def downgrade() -> None:
    op.drop_table("benchmark_field_candidates")
    op.drop_table("benchmark_difference_reviews")
    op.drop_table("benchmark_results")
    op.drop_table("benchmark_runs")
    op.drop_table("benchmark_manifests")
