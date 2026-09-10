"""Add immutable parser provenance and audited activation history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_28"
down_revision: str | None = "20260910_27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("parser_releases") as batch:
        batch.add_column(sa.Column("parser_version", sa.String(100), nullable=True))
        batch.add_column(sa.Column("git_commit", sa.String(64), nullable=True))
        batch.add_column(sa.Column("parser_content_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("configuration_hash", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("dependency_lock_hash", sa.String(64), nullable=True)
        )
        batch.add_column(sa.Column("deployable_digest", sa.String(80), nullable=True))
        batch.add_column(
            sa.Column("qualifying_benchmark_run", sa.String(100), nullable=True)
        )
        batch.add_column(
            sa.Column("status", sa.String(12), nullable=False, server_default="draft")
        )
        batch.create_check_constraint(
            "ck_parser_release_status",
            "status IN ('draft', 'active', 'retired', 'revoked')",
        )
    op.execute(
        "UPDATE parser_releases SET status = 'active' WHERE EXISTS "
        "(SELECT 1 FROM portal_parser_activations a "
        "WHERE a.source = parser_releases.source "
        "AND a.release_hash = parser_releases.release_hash)"
    )
    op.create_table(
        "parser_activation_audits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("activation_epoch", sa.Integer(), nullable=False),
        sa.Column(
            "previous_release_hash",
            sa.String(64),
            sa.ForeignKey("parser_releases.release_hash"),
            nullable=True,
        ),
        sa.Column(
            "release_hash",
            sa.String(64),
            sa.ForeignKey("parser_releases.release_hash"),
            nullable=False,
        ),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("compared_metrics", sa.String(1000), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('activate', 'rollback')", name="ck_parser_activation_action"
        ),
        sa.CheckConstraint("activation_epoch > 0", name="ck_parser_audit_epoch"),
        sa.UniqueConstraint("source", "activation_epoch"),
    )


def downgrade() -> None:
    op.drop_table("parser_activation_audits")
    with op.batch_alter_table("parser_releases") as batch:
        batch.drop_constraint("ck_parser_release_status", type_="check")
        for column in (
            "status",
            "qualifying_benchmark_run",
            "deployable_digest",
            "dependency_lock_hash",
            "configuration_hash",
            "parser_content_hash",
            "git_commit",
            "parser_version",
        ):
            batch.drop_column(column)
