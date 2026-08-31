"""Add renovation scope, comparable evidence, and attachment metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_08"
down_revision: str | None = "20260831_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "renovation_scope_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("property_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("low_minor", sa.Integer(), nullable=False),
        sa.Column("base_minor", sa.Integer(), nullable=False),
        sa.Column("high_minor", sa.Integer(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("property_id", "name"),
    )
    op.create_index(
        "ix_renovation_scope_items_property_id",
        "renovation_scope_items",
        ["property_id"],
    )
    op.create_table(
        "renovation_comparables",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("property_id", sa.String(100), nullable=False),
        sa.Column("comparable_id", sa.String(100), nullable=False),
        sa.Column("effective_move_in_minor", sa.Integer(), nullable=False),
        sa.Column("similarity", sa.Numeric(4, 3), nullable=False),
        sa.Column("evidence_source", sa.String(255), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("property_id", "comparable_id"),
    )
    op.create_index(
        "ix_renovation_comparables_property_id",
        "renovation_comparables",
        ["property_id"],
    )
    op.create_table(
        "renovation_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("property_id", sa.String(100), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False, unique=True),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_renovation_attachments_property_id",
        "renovation_attachments",
        ["property_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_renovation_attachments_property_id", table_name="renovation_attachments"
    )
    op.drop_table("renovation_attachments")
    op.drop_index(
        "ix_renovation_comparables_property_id", table_name="renovation_comparables"
    )
    op.drop_table("renovation_comparables")
    op.drop_index(
        "ix_renovation_scope_items_property_id", table_name="renovation_scope_items"
    )
    op.drop_table("renovation_scope_items")
