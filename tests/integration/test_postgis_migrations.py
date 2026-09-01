import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.mark.postgres
@pytest.mark.skipif(POSTGRES_URL is None, reason="TEST_POSTGRES_URL is not configured")
def test_migrations_create_catalog_in_postgis(monkeypatch: pytest.MonkeyPatch) -> None:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    config = Config("alembic.ini")

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    try:
        engine = create_engine(POSTGRES_URL)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT PostGIS_Version()"))
            assert {
                "sources",
                "source_messages",
                "listings",
                "listing_snapshots",
                "property_candidates",
            } <= set(inspect(connection).get_table_names(schema="public"))
    finally:
        command.downgrade(config, "base")
