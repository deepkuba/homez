"""Enable PostGIS for geospatial catalog data."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260830_02"
down_revision: str | None = "20260830_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    # PostGIS may own shared objects or data; application rollback must not drop it.
    pass
