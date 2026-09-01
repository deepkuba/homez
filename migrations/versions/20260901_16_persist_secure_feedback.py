"""Add persistent feedback scope and rate-limit audit fields."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_16"
down_revision: str | None = "20260901_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("feedback_tokens") as batch_op:
        batch_op.add_column(
            sa.Column("scope", sa.String(30), nullable=False, server_default="feedback")
        )
        batch_op.add_column(
            sa.Column(
                "issued_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch_op.create_unique_constraint(
            "uq_feedback_tokens_report_listing_scope",
            ["report_id", "listing_id", "scope"],
        )
    with op.batch_alter_table("feedback_events") as batch_op:
        batch_op.add_column(
            sa.Column(
                "actor_hash", sa.String(64), nullable=False, server_default="legacy"
            )
        )
        batch_op.create_index("ix_feedback_events_actor_hash", ["actor_hash"])


def downgrade() -> None:
    with op.batch_alter_table("feedback_events") as batch_op:
        batch_op.drop_index("ix_feedback_events_actor_hash")
        batch_op.drop_column("actor_hash")
    with op.batch_alter_table("feedback_tokens") as batch_op:
        batch_op.drop_constraint(
            "uq_feedback_tokens_report_listing_scope", type_="unique"
        )
        batch_op.drop_column("issued_at")
        batch_op.drop_column("scope")
