"""Add durable workflow, match artifacts, and report drafts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_14"
down_revision: str | None = "20260901_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_presentations", sa.Column("report_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "candidate_presentations", sa.Column("section", sa.String(20), nullable=True)
    )
    op.add_column(
        "candidate_presentations",
        sa.Column("material_fingerprint", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_candidate_presentations_report_id", "candidate_presentations", ["report_id"]
    )
    op.create_table(
        "workflow_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("idempotency_key", sa.String(500), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(200), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parent_job_id", sa.Uuid(), nullable=True),
        sa.Column("root_job_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_detail", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["parent_job_id"], ["workflow_jobs.id"]),
        sa.ForeignKeyConstraint(["root_job_id"], ["workflow_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("lease_token"),
    )
    op.create_index("ix_workflow_jobs_kind", "workflow_jobs", ["kind"])
    op.create_index("ix_workflow_jobs_state", "workflow_jobs", ["state"])
    op.create_index(
        "ix_workflow_jobs_lease_expires_at", "workflow_jobs", ["lease_expires_at"]
    )
    op.create_index("ix_workflow_jobs_root_job_id", "workflow_jobs", ["root_job_id"])
    op.create_index(
        "ix_workflow_jobs_claim", "workflow_jobs", ["state", "available_at", "priority"]
    )
    op.create_table(
        "workflow_job_attempts",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("worker_id", sa.String(200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(30), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_detail", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["workflow_jobs.id"]),
        sa.PrimaryKeyConstraint("job_id", "attempt_number"),
        sa.UniqueConstraint("lease_token"),
    )
    op.create_table(
        "candidate_fact_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("normalizer_version", sa.String(50), nullable=False),
        sa.Column("facts_schema_version", sa.Integer(), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False),
        sa.Column("facts_hash", sa.String(64), nullable=False),
        sa.Column("material_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["property_candidates.id"]),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["listing_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", "snapshot_id", "normalizer_version"),
    )
    op.create_index(
        "ix_candidate_fact_sets_candidate_id", "candidate_fact_sets", ["candidate_id"]
    )
    op.create_table(
        "candidate_match_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("fact_set_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_profile_version", sa.Integer(), nullable=False),
        sa.Column("routing_goal_version", sa.Integer(), nullable=False),
        sa.Column("matcher_version", sa.String(50), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False),
        sa.Column("explanation_json", sa.Text(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("contains_unknown_hard_rule", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Numeric(8, 3), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["property_candidates.id"]),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["listing_snapshots.id"]),
        sa.ForeignKeyConstraint(["fact_set_id"], ["candidate_fact_sets.id"]),
        sa.ForeignKeyConstraint(["buyer_profile_version"], ["buyer_profiles.version"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("input_fingerprint"),
    )
    op.create_index(
        "ix_candidate_match_evaluations_candidate_id",
        "candidate_match_evaluations",
        ["candidate_id"],
    )
    op.create_table(
        "report_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_key", sa.String(64), nullable=False),
        sa.Column("period", sa.String(8), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("buyer_profile_version", sa.Integer(), nullable=False),
        sa.Column("routing_goal_version", sa.Integer(), nullable=False),
        sa.Column("selection_version", sa.String(50), nullable=False),
        sa.Column("render_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["buyer_profile_version"], ["buyer_profiles.version"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_key"),
    )
    op.create_index("ix_report_drafts_period", "report_drafts", ["period"])
    op.create_table(
        "report_items",
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.String(20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("material_fingerprint", sa.String(64), nullable=False),
        sa.Column("selection_reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["report_drafts.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["property_candidates.id"]),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["listing_snapshots.id"]),
        sa.ForeignKeyConstraint(["evaluation_id"], ["candidate_match_evaluations.id"]),
        sa.PrimaryKeyConstraint("report_id", "section", "position"),
        sa.UniqueConstraint("report_id", "candidate_id"),
    )


def downgrade() -> None:
    op.drop_table("report_items")
    op.drop_index("ix_report_drafts_period", table_name="report_drafts")
    op.drop_table("report_drafts")
    op.drop_index(
        "ix_candidate_match_evaluations_candidate_id",
        table_name="candidate_match_evaluations",
    )
    op.drop_table("candidate_match_evaluations")
    op.drop_index(
        "ix_candidate_fact_sets_candidate_id", table_name="candidate_fact_sets"
    )
    op.drop_table("candidate_fact_sets")
    op.drop_table("workflow_job_attempts")
    op.drop_index("ix_workflow_jobs_claim", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_root_job_id", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_lease_expires_at", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_state", table_name="workflow_jobs")
    op.drop_index("ix_workflow_jobs_kind", table_name="workflow_jobs")
    op.drop_table("workflow_jobs")
    op.drop_index(
        "ix_candidate_presentations_report_id", table_name="candidate_presentations"
    )
    with op.batch_alter_table("candidate_presentations") as batch_op:
        batch_op.drop_column("material_fingerprint")
        batch_op.drop_column("section")
        batch_op.drop_column("report_id")
