"""Production-only parsed handoff for asynchronous queue normalization."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_24"
down_revision: str | None = "20260909_23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_parser_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("scrape_tasks.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "capture_id", sa.Uuid(), sa.ForeignKey("page_captures.id"), nullable=False
        ),
        sa.Column(
            "release_hash",
            sa.String(64),
            sa.ForeignKey("parser_releases.release_hash"),
            nullable=False,
        ),
        sa.Column("activation_epoch", sa.Integer(), nullable=False),
        sa.Column("variant", sa.String(80), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False),
        sa.Column("missing_fields_json", sa.String(2048), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "production_field_candidates",
        sa.Column(
            "result_id",
            sa.Uuid(),
            sa.ForeignKey("production_parser_results.id"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(80), nullable=False),
        sa.Column("locator", sa.String(200), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("production_field_candidates")
    op.drop_table("production_parser_results")
