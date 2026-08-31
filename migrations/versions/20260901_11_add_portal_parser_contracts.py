"""Record parser versions and support multi-listing alert messages."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_11"
down_revision: str | None = "20260831_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_messages",
        sa.Column(
            "parser_version",
            sa.String(length=100),
            nullable=False,
            server_default="legacy-v1",
        ),
    )
    op.add_column(
        "quarantined_messages",
        sa.Column(
            "parser_version",
            sa.String(length=100),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_table(
        "source_message_items",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["source_messages.id"]),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["listing_snapshots.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["property_candidates.id"]),
        sa.PrimaryKeyConstraint("message_id", "listing_id"),
        sa.UniqueConstraint(
            "message_id", "position", name="uq_source_message_items_position"
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO source_message_items "
            "(message_id, listing_id, position, snapshot_id, candidate_id) "
            "SELECT id, listing_id, 0, snapshot_id, candidate_id "
            "FROM source_messages"
        )
    )


def downgrade() -> None:
    op.drop_table("source_message_items")
    with op.batch_alter_table("quarantined_messages") as batch_op:
        batch_op.drop_column("parser_version")
    with op.batch_alter_table("source_messages") as batch_op:
        batch_op.drop_column("parser_version")
