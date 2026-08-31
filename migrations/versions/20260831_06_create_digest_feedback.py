"""Add idempotent digest delivery and scoped feedback records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_06"
down_revision: str | None = "20260831_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "digest_deliveries",
        sa.Column("period", sa.String(8), primary_key=True),
        sa.Column("report_id", sa.String(100), nullable=False, unique=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
    )
    op.create_table(
        "feedback_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("report_id", sa.String(100), nullable=False),
        sa.Column("listing_id", sa.String(100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_feedback_tokens_report_id", "feedback_tokens", ["report_id"])
    op.create_index("ix_feedback_tokens_listing_id", "feedback_tokens", ["listing_id"])
    op.create_table(
        "feedback_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "token_hash",
            sa.String(64),
            sa.ForeignKey("feedback_tokens.token_hash"),
            nullable=False,
        ),
        sa.Column("report_id", sa.String(100), nullable=False),
        sa.Column("listing_id", sa.String(100), nullable=False),
        sa.Column("value", sa.String(20), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("feedback_events")
    op.drop_index("ix_feedback_tokens_listing_id", table_name="feedback_tokens")
    op.drop_index("ix_feedback_tokens_report_id", table_name="feedback_tokens")
    op.drop_table("feedback_tokens")
    op.drop_table("digest_deliveries")
