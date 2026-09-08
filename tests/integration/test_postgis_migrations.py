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
            assert "latest_offers" in inspect(connection).get_view_names(
                schema="analytics"
            )
            columns = {
                column["name"]
                for column in inspect(connection).get_columns(
                    "latest_offers", schema="analytics"
                )
            }
            assert {
                "listing_id",
                "source",
                "canonical_url",
                "purchase_price_pln",
                "monthly_admin_fee_pln",
                "heating_type",
                "feedback_value",
                "facts",
                "explanation",
            } <= columns
            role = connection.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin, "
                    "rolconnlimit, rolconfig FROM pg_roles "
                    "WHERE rolname = 'homez_analytics_reader'"
                )
            ).one()
            assert role[:5] == (False, False, False, True, 2)
            assert "default_transaction_read_only=on" in role.rolconfig
            assert connection.scalar(
                text(
                    "SELECT has_table_privilege("
                    "'homez_analytics_reader', "
                    "'analytics.latest_offers', 'SELECT')"
                )
            )
            assert not connection.scalar(
                text(
                    "SELECT has_table_privilege("
                    "'homez_analytics_reader', 'public.listings', 'SELECT')"
                )
            )
    finally:
        command.downgrade(config, "base")
