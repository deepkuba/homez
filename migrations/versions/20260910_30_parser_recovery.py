"""Add replay provenance, lifecycle evidence, and network release audits."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_30"
down_revision: str | None = "20260910_29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("listings") as batch:
        batch.add_column(
            sa.Column(
                "lifecycle_state",
                sa.String(12),
                nullable=False,
                server_default="active",
            )
        )
        batch.add_column(sa.Column("lifecycle_evidence", sa.String(80), nullable=True))
        batch.create_check_constraint(
            "ck_listing_lifecycle", "lifecycle_state IN ('active', 'stale', 'inactive')"
        )
    with op.batch_alter_table("scrape_tasks") as batch:
        batch.drop_constraint("ck_scrape_task_state", type_="check")
        batch.create_check_constraint(
            "ck_scrape_task_state",
            "state IN ('pending', 'running', 'deferred', 'succeeded', 'failed', "
            "'held', 'superseded', 'cancelled')",
        )
    op.create_table(
        "artifact_recovery_bindings",
        sa.Column(
            "task_id", sa.Uuid(), sa.ForeignKey("scrape_tasks.id"), primary_key=True
        ),
        sa.Column(
            "capture_id", sa.Uuid(), sa.ForeignKey("page_captures.id"), nullable=False
        ),
        sa.Column("artifact_id", sa.String(36), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "network_recovery_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("release_hash", sa.String(64), nullable=False),
        sa.Column("activation_epoch", sa.Integer(), nullable=False),
        sa.Column("batch", sa.String(12), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        sa.Column("released_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "network_recovery_campaigns",
        sa.Column("source", sa.String(20), primary_key=True),
        sa.Column("release_hash", sa.String(64), primary_key=True),
        sa.Column("next_batch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("paused_reason", sa.String(40), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("network_recovery_campaigns")
    op.drop_table("network_recovery_releases")
    op.drop_table("artifact_recovery_bindings")
    with op.batch_alter_table("scrape_tasks") as batch:
        batch.drop_constraint("ck_scrape_task_state", type_="check")
        batch.create_check_constraint(
            "ck_scrape_task_state",
            "state IN ('pending', 'running', 'deferred', 'succeeded', "
            "'failed', 'held')",
        )
    with op.batch_alter_table("listings") as batch:
        batch.drop_constraint("ck_listing_lifecycle", type_="check")
        batch.drop_column("lifecycle_evidence")
        batch.drop_column("lifecycle_state")
