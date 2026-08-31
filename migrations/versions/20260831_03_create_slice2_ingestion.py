"""Create Slice 2 ingestion state and quarantine tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_03"
down_revision: str | None = "20260830_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quarantined_messages",
        sa.Column("provider_message_id", sa.String(length=255), nullable=False),
        sa.Column("source_key", sa.String(length=100), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_message", sa.LargeBinary(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("provider_message_id"),
    )
    op.create_index(
        "ix_quarantined_messages_source_key",
        "quarantined_messages",
        ["source_key"],
    )
    op.create_table(
        "ingestion_states",
        sa.Column("source_key", sa.String(length=100), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("source_key"),
    )


def downgrade() -> None:
    op.drop_table("ingestion_states")
    op.drop_index(
        "ix_quarantined_messages_source_key", table_name="quarantined_messages"
    )
    op.drop_table("quarantined_messages")
