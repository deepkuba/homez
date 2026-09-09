"""Expand central queue leases, attempts, worker capabilities, and epoch fencing."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_23"
down_revision: str | None = "20260909_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "portal_parser_activations",
        sa.Column("source", sa.String(20), primary_key=True),
        sa.Column("release_hash", sa.String(64), nullable=False),
        sa.Column("activation_epoch", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source", "release_hash"],
            ["parser_releases.source", "parser_releases.release_hash"],
        ),
        sa.CheckConstraint("activation_epoch > 0", name="ck_portal_activation_epoch"),
    )
    op.create_table(
        "scraper_workers",
        sa.Column("worker_id", sa.String(80), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("deployment", sa.String(3), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("release_hashes_json", sa.String(4500), nullable=False),
        sa.Column("healthy", sa.Boolean(), nullable=False),
        sa.CheckConstraint("deployment IN ('nas', 'vps')", name="ck_worker_deployment"),
        sa.CheckConstraint(
            "source IN ('gratka', 'morizon', 'otodom', 'olx')",
            name="ck_worker_source",
        ),
    )
    with op.batch_alter_table("scrape_tasks") as batch:
        batch.add_column(
            sa.Column("state", sa.String(20), nullable=False, server_default="pending")
        )
        batch.add_column(
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        for name in ("available_at", "created_at"):
            batch.add_column(
                sa.Column(
                    name,
                    sa.DateTime(timezone=True),
                    nullable=False,
                    server_default=sa.func.now(),
                )
            )
        batch.add_column(sa.Column("lease_owner", sa.String(80), nullable=True))
        batch.add_column(sa.Column("lease_token", sa.Uuid(), nullable=True))
        for name in ("lease_expires_at", "lease_started_at", "finished_at"):
            batch.add_column(sa.Column(name, sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(
            "ck_scrape_task_state",
            "state IN ('pending', 'running', 'deferred', "
            "'succeeded', 'failed', 'held')",
        )
        batch.create_check_constraint(
            "ck_scrape_task_attempt_count", "attempt_count >= 0"
        )
        batch.create_foreign_key(
            "fk_scrape_task_source_release",
            "parser_releases",
            ["source", "release_hash"],
            ["source", "release_hash"],
        )
        batch.create_index(
            "ix_scrape_tasks_claim", ["source", "state", "priority", "available_at"]
        )
        batch.create_index("ix_scrape_tasks_status", ["source", "created_at", "id"])
    with op.batch_alter_table("scrape_tasks") as batch:
        for name in ("available_at", "created_at"):
            batch.alter_column(name, server_default=None)
    op.create_table(
        "scrape_attempts",
        sa.Column(
            "task_id", sa.Uuid(), sa.ForeignKey("scrape_tasks.id"), primary_key=True
        ),
        sa.Column("attempt_number", sa.Integer(), primary_key=True),
        sa.Column("lease_token", sa.Uuid(), nullable=False, unique=True),
        sa.Column(
            "worker_id",
            sa.String(80),
            sa.ForeignKey("scraper_workers.worker_id"),
            nullable=False,
        ),
        sa.Column("release_hash", sa.String(64), nullable=False),
        sa.Column("activation_epoch", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("code", sa.String(40), nullable=True),
        sa.Column(
            "route_class", sa.String(12), nullable=False, server_default="unassigned"
        ),
        sa.Column("route_id", sa.Uuid(), nullable=True),
        sa.Column("response_bytes", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("scrape_attempts")
    with op.batch_alter_table("scrape_tasks") as batch:
        batch.drop_index("ix_scrape_tasks_status")
        batch.drop_index("ix_scrape_tasks_claim")
        batch.drop_constraint("fk_scrape_task_source_release", type_="foreignkey")
        batch.drop_constraint("ck_scrape_task_state", type_="check")
        batch.drop_constraint("ck_scrape_task_attempt_count", type_="check")
        for name in (
            "state",
            "priority",
            "attempt_count",
            "available_at",
            "created_at",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
            "lease_started_at",
            "finished_at",
        ):
            batch.drop_column(name)
    op.drop_table("scraper_workers")
    op.drop_table("portal_parser_activations")
