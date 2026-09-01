"""Turn the digest ledger into a persistent delivery outbox."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_15"
down_revision: str | None = "20260901_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("digest_deliveries") as batch_op:
        batch_op.alter_column(
            "sent_at", existing_type=sa.DateTime(timezone=True), nullable=True
        )
        batch_op.add_column(
            sa.Column(
                "render_version", sa.String(50), nullable=False, server_default="legacy"
            )
        )
        batch_op.add_column(
            sa.Column("state", sa.String(30), nullable=False, server_default="sent")
        )
        batch_op.add_column(
            sa.Column(
                "next_attempt_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch_op.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("claim_token", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("provider_message_id", sa.String(255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("last_error", sa.String(500), nullable=True))
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch_op.create_unique_constraint(
            "uq_digest_deliveries_claim_token", ["claim_token"]
        )
        batch_op.create_unique_constraint(
            "uq_digest_deliveries_provider_message_id", ["provider_message_id"]
        )
        batch_op.create_index("ix_digest_deliveries_state", ["state"])
        batch_op.create_index(
            "ix_digest_deliveries_next_attempt_at", ["next_attempt_at"]
        )


def downgrade() -> None:
    op.execute("DELETE FROM digest_deliveries WHERE sent_at IS NULL")
    with op.batch_alter_table("digest_deliveries") as batch_op:
        batch_op.drop_index("ix_digest_deliveries_next_attempt_at")
        batch_op.drop_index("ix_digest_deliveries_state")
        batch_op.drop_constraint(
            "uq_digest_deliveries_provider_message_id", type_="unique"
        )
        batch_op.drop_constraint("uq_digest_deliveries_claim_token", type_="unique")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
        batch_op.drop_column("last_error")
        batch_op.drop_column("acknowledged_at")
        batch_op.drop_column("provider_message_id")
        batch_op.drop_column("claimed_at")
        batch_op.drop_column("claim_token")
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("state")
        batch_op.drop_column("render_version")
        batch_op.alter_column(
            "sent_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )
