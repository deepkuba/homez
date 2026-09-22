"""Add artifact-only parser bootstrap discovery captures."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_33"
down_revision: str | None = "20260914_32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scrape_tasks") as batch:
        batch.drop_constraint("ck_scrape_task_class", type_="check")
        batch.create_check_constraint(
            "ck_scrape_task_class",
            "task_class IN ('live', 'artifact_recovery', 'network_recovery', "
            "'discovery_capture')",
        )
    op.create_table(
        "discovery_captures",
        sa.Column(
            "task_id", sa.Uuid(), sa.ForeignKey("scrape_tasks.id"), primary_key=True
        ),
        sa.Column(
            "capture_id",
            sa.Uuid(),
            sa.ForeignKey("page_captures.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("artifact_id", sa.String(36), nullable=False, unique=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('gratka', 'morizon', 'otodom', 'olx')",
            name="ck_discovery_capture_source",
        ),
    )
    op.create_table(
        "discovery_canary_audits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "release_hash",
            sa.String(64),
            sa.ForeignKey("parser_releases.release_hash"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("enqueued_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("discovery_canary_audits")
    op.drop_table("discovery_captures")
    with op.batch_alter_table("scrape_tasks") as batch:
        batch.drop_constraint("ck_scrape_task_class", type_="check")
        batch.create_check_constraint(
            "ck_scrape_task_class",
            "task_class IN ('live', 'artifact_recovery', 'network_recovery')",
        )
