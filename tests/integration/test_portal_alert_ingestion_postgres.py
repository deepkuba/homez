import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.catalog.orm import (
    ListingRecord,
    ListingSnapshotRecord,
    SourceMessageRecord,
)
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.sources.portal_alerts import (
    GratkaAlertParser,
    MorizonAlertParser,
    OtodomAlertParser,
)

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")
FIXTURES = Path(__file__).parents[2] / "data" / "email_examples"


@pytest.mark.postgres
@pytest.mark.skipif(POSTGRES_URL is None, reason="TEST_POSTGRES_URL is not configured")
def test_approved_portals_ingest_idempotently_in_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(POSTGRES_URL)
    try:
        with Session(engine) as session:
            for source_key, parser in (
                ("otodom", OtodomAlertParser()),
                ("morizon", MorizonAlertParser()),
                ("gratka", GratkaAlertParser()),
            ):
                service = AlertIngestionService(
                    parser=parser,
                    catalog=SqlAlchemyCatalogRepository(session),
                )
                raw = (FIXTURES / f"{source_key}_alert.eml").read_bytes()
                assert service.ingest(raw).created is True
                assert service.ingest(raw).created is False

            assert (
                session.scalar(select(func.count()).select_from(SourceMessageRecord))
                == 3
            )
            assert session.scalar(select(func.count()).select_from(ListingRecord)) == 3
            assert (
                session.scalar(select(func.count()).select_from(ListingSnapshotRecord))
                == 3
            )
    finally:
        engine.dispose()
        command.downgrade(config, "base")
