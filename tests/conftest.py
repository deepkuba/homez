import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from homefinder.catalog.orm import Base


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1" and not os.environ.get(
        "TEST_POSTGRES_URL"
    ):
        raise pytest.UsageError(
            "TEST_POSTGRES_URL is required for the PostgreSQL release gate"
        )


@pytest.fixture(autouse=True)
def isolate_postgres_test(request: pytest.FixtureRequest):
    if request.node.get_closest_marker("postgres") is None:
        yield
        return
    database_url = os.environ.get("TEST_POSTGRES_URL")
    if database_url is None:
        yield
        return
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        _truncate_application_tables(engine)
        yield
    finally:
        command.upgrade(config, "head")
        _truncate_application_tables(engine)
        engine.dispose()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _truncate_application_tables(engine) -> None:  # type: ignore[no-untyped-def]
    quote = engine.dialect.identifier_preparer.quote
    tables = ", ".join(quote(table.name) for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")
