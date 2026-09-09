"""Expand dark scrape queue, immutable capture, and parser release identities."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_22"
down_revision: str | None = "20260908_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "parser_releases",
        sa.Column("release_hash", sa.String(64), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('gratka', 'morizon', 'otodom', 'olx')",
            name="ck_parser_release_source",
        ),
        sa.UniqueConstraint("source", "release_hash"),
    )
    op.create_table(
        "page_captures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.Uuid(),
            sa.ForeignKey("listing_snapshots.id"),
            nullable=False,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "size_bytes >= 0 AND size_bytes <= 2000000", name="ck_page_capture_size"
        ),
    )
    op.create_table(
        "scrape_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "snapshot_id",
            sa.Uuid(),
            sa.ForeignKey("listing_snapshots.id"),
            nullable=False,
        ),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("task_class", sa.String(30), nullable=False),
        sa.Column(
            "release_hash",
            sa.String(64),
            sa.ForeignKey("parser_releases.release_hash"),
            nullable=False,
        ),
        sa.Column("activation_epoch", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.CheckConstraint(
            "source IN ('gratka', 'morizon', 'otodom', 'olx')",
            name="ck_scrape_task_source",
        ),
        sa.CheckConstraint(
            "task_class IN ('live', 'artifact_recovery', 'network_recovery')",
            name="ck_scrape_task_class",
        ),
        sa.CheckConstraint("activation_epoch > 0", name="ck_scrape_task_epoch"),
    )


def downgrade() -> None:
    op.drop_table("scrape_tasks")
    op.drop_table("page_captures")
    op.drop_table("parser_releases")
