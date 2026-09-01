"""Persist Gmail labels and durable source health."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_12"
down_revision: str | None = "20260901_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_states",
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_states",
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_states",
        sa.Column("last_quarantine_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_states",
        sa.Column("status", sa.String(20), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "ingestion_states",
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "ingestion_states",
        sa.Column("quarantine_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "gmail_label_bindings",
        sa.Column("mailbox_key", sa.String(100), nullable=False),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("label_name", sa.String(225), nullable=False),
        sa.Column("label_id", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("mailbox_key", "source_key", "role"),
        sa.UniqueConstraint(
            "mailbox_key",
            "source_key",
            "label_name",
            name="uq_gmail_label_binding_name",
        ),
    )


def downgrade() -> None:
    op.drop_table("gmail_label_bindings")
    with op.batch_alter_table("ingestion_states") as batch_op:
        batch_op.drop_column("quarantine_count")
        batch_op.drop_column("consecutive_failures")
        batch_op.drop_column("status")
        batch_op.drop_column("last_quarantine_at")
        batch_op.drop_column("last_error_at")
        batch_op.drop_column("last_poll_at")
