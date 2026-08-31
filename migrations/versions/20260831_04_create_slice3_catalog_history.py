"""Add Slice 3 identity, duplicate review, and resurfacing history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_04"
down_revision: str | None = "20260831_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "property_candidates",
        sa.Column("deterministic_key", sa.String(length=500), nullable=True),
    )
    # Existing Slice 1/2 rows are assigned a reviewable legacy key. New writes
    # always use the normalized catalog fingerprint.
    op.execute(
        "UPDATE property_candidates SET deterministic_key = "
        "'legacy:' || CAST(id AS VARCHAR(36)) "
        "WHERE deterministic_key IS NULL"
    )
    with op.batch_alter_table("property_candidates") as batch_op:
        batch_op.alter_column("deterministic_key", nullable=False)
    with op.batch_alter_table("property_candidates") as batch_op:
        batch_op.create_unique_constraint(
            "uq_property_candidates_deterministic_key", ["deterministic_key"]
        )
    op.create_table(
        "duplicate_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("possible_listing_id", sa.Uuid(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("reasons", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.CheckConstraint("listing_id <> possible_listing_id"),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.ForeignKeyConstraint(["possible_listing_id"], ["listings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("listing_id", "possible_listing_id"),
    )
    op.create_index(
        "ix_duplicate_evidence_listing_id", "duplicate_evidence", ["listing_id"]
    )
    op.create_index(
        "ix_duplicate_evidence_possible_listing_id",
        "duplicate_evidence",
        ["possible_listing_id"],
    )
    op.create_table(
        "candidate_presentations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("presented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["candidate_id"], ["property_candidates.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["listing_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_candidate_presentations_candidate_id",
        "candidate_presentations",
        ["candidate_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_presentations_candidate_id", table_name="candidate_presentations"
    )
    op.drop_table("candidate_presentations")
    op.drop_index(
        "ix_duplicate_evidence_possible_listing_id", table_name="duplicate_evidence"
    )
    op.drop_index("ix_duplicate_evidence_listing_id", table_name="duplicate_evidence")
    op.drop_table("duplicate_evidence")
    with op.batch_alter_table("property_candidates") as batch_op:
        batch_op.drop_constraint(
            "uq_property_candidates_deterministic_key", type_="unique"
        )
    op.drop_column("property_candidates", "deterministic_key")
