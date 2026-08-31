"""Add primary-market entities, evidence, risk dimensions, and review tasks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_09"
down_revision: str | None = "20260831_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "primary_market_projects",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normal_eligibility", sa.String(20), nullable=False),
        sa.Column("overall_concern", sa.String(30), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "primary_market_entities",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("project_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("registration_reference", sa.String(255)),
    )
    op.create_index(
        "ix_primary_market_entities_project_id",
        "primary_market_entities",
        ["project_id"],
    )
    op.create_table(
        "primary_market_evidence",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("project_id", sa.String(100), nullable=False),
        sa.Column("subject_id", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("reference", sa.String(500), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("permitted", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_primary_market_evidence_project_id",
        "primary_market_evidence",
        ["project_id"],
    )
    op.create_table(
        "primary_market_risks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.String(100), nullable=False),
        sa.Column("dimension", sa.String(60), nullable=False),
        sa.Column("level", sa.String(30), nullable=False),
        sa.Column("facts", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_primary_market_risks_project_id", "primary_market_risks", ["project_id"]
    )
    op.create_table(
        "primary_market_manual_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.String(100), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "subject", "reason"),
    )
    op.create_index(
        "ix_primary_market_tasks_status", "primary_market_manual_tasks", ["status"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_primary_market_tasks_status", table_name="primary_market_manual_tasks"
    )
    op.drop_table("primary_market_manual_tasks")
    op.drop_index(
        "ix_primary_market_risks_project_id", table_name="primary_market_risks"
    )
    op.drop_table("primary_market_risks")
    op.drop_index(
        "ix_primary_market_evidence_project_id", table_name="primary_market_evidence"
    )
    op.drop_table("primary_market_evidence")
    op.drop_index(
        "ix_primary_market_entities_project_id", table_name="primary_market_entities"
    )
    op.drop_table("primary_market_entities")
    op.drop_table("primary_market_projects")
