"""Expand proxy billing-cycle keys for anchored start dates."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260914_32"
down_revision: str | None = "20260910_31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("proxy_usage_ledger") as batch:
        batch.alter_column(
            "billing_cycle",
            existing_type=sa.String(7),
            type_=sa.String(10),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("proxy_usage_ledger") as batch:
        batch.alter_column(
            "billing_cycle",
            existing_type=sa.String(10),
            type_=sa.String(7),
            existing_nullable=False,
        )
