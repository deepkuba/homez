import json
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from homefinder.catalog.orm import Base


@pytest.fixture
def source_budget_policy_file(tmp_path):
    path = tmp_path / "source-budget.json"
    path.write_text(
        json.dumps(
            {
                "billing_cycle_anchor_day": 15,
                "portals": {
                    source: {
                        "minimum_interval_seconds": 10,
                        "daily_attempt_limit": 1100,
                        "daily_success_limit": 1000,
                        "policy_version": "synthetic-reviewed-v1",
                    }
                    for source in ("gratka", "morizon", "otodom", "olx")
                },
            }
        )
    )
    return path


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


@pytest.fixture
def scrape_queue(request, tmp_path):
    """Synthetic queue only; no source page or real service credentials."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from sqlalchemy.orm import sessionmaker

    from homefinder.catalog.orm import (
        ListingRecord,
        ListingSnapshotRecord,
        ParserReleaseRecord,
        PortalParserActivationRecord,
        SourceRecord,
    )
    from homefinder.scrape_queue.contracts import WorkerIdentity
    from homefinder.scrape_queue.repository import ScrapeQueueRepository

    url = (
        os.environ["TEST_POSTGRES_URL"]
        if request.node.get_closest_marker("postgres")
        else f"sqlite:///{tmp_path / 'scrape.sqlite3'}"
    )
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    snapshots = []
    with sessions.begin() as session:
        for source, release in (("gratka", "a" * 64), ("morizon", "b" * 64)):
            source_id = uuid4()
            session.add(SourceRecord(id=source_id, key=source, display_name=source))
            session.add(
                ParserReleaseRecord(source=source, release_hash=release, created_at=now)
            )
            session.flush()
            session.add(
                PortalParserActivationRecord(
                    source=source, release_hash=release, activation_epoch=1
                )
            )
            if source != "gratka":
                continue
            for index in range(3):
                listing_id, snapshot_id = uuid4(), uuid4()
                session.add(
                    ListingRecord(
                        id=listing_id,
                        source_id=source_id,
                        source_listing_id=str(10000001 + index),
                        canonical_url=(
                            "https://gratka.pl/nieruchomosci/test/ob/"
                            f"{10000001 + index}"
                        ),
                        title="Synthetic example",
                    )
                )
                session.flush()
                session.add(
                    ListingSnapshotRecord(
                        id=snapshot_id,
                        listing_id=listing_id,
                        observed_at=now,
                        price_minor=100,
                        currency="PLN",
                        availability="active",
                        description="Synthetic example",
                        content_hash=str(index) * 64,
                    )
                )
                snapshots.append(snapshot_id)
    repository = ScrapeQueueRepository(sessions)
    workers = [
        WorkerIdentity(
            worker_id=f"test-{deployment}", source="gratka", deployment=deployment
        )
        for deployment in ("nas", "vps")
    ]
    for worker in workers:
        repository.register_worker(worker, release_hashes=("a" * 64,), now=now)
    try:
        yield repository, snapshots, workers, sessions
    finally:
        engine.dispose()
