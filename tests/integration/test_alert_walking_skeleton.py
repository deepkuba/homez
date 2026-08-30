from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.catalog.orm import (
    Base,
    ListingRecord,
    ListingSnapshotRecord,
    PropertyCandidateRecord,
    SourceMessageRecord,
)
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.sources.errors import AlertParseError
from homefinder.sources.sample_portal import SamplePortalAlertParser

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sample_portal"


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_fixture_reaches_normalized_catalog_and_preview_idempotently(
    session: Session,
) -> None:
    service = AlertIngestionService(
        parser=SamplePortalAlertParser(),
        catalog=SqlAlchemyCatalogRepository(session),
    )
    raw = (FIXTURES / "valid_alert.eml").read_bytes()

    first = service.ingest(raw)
    second = service.ingest(raw)

    assert first.created is True
    assert second.created is False
    assert first.preview_html == second.preview_html
    assert "Jasne 2 pokoje z balkonem" in first.preview_html
    assert "749 000,00 zł" in first.preview_html
    assert count(session, SourceMessageRecord) == 1
    assert count(session, ListingRecord) == 1
    assert count(session, ListingSnapshotRecord) == 1
    assert count(session, PropertyCandidateRecord) == 1


def test_malformed_alert_leaves_catalog_unchanged(session: Session) -> None:
    service = AlertIngestionService(
        parser=SamplePortalAlertParser(),
        catalog=SqlAlchemyCatalogRepository(session),
    )

    with pytest.raises(AlertParseError):
        service.ingest((FIXTURES / "malformed_alert.eml").read_bytes())

    assert count(session, SourceMessageRecord) == 0
    assert count(session, ListingRecord) == 0
    assert count(session, ListingSnapshotRecord) == 0
    assert count(session, PropertyCandidateRecord) == 0
