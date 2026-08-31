from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.application.poll_gmail import GmailPollingService
from homefinder.catalog.orm import (
    Base,
    ListingRecord,
    ListingSnapshotRecord,
    QuarantinedMessageRecord,
    SourceMessageItemRecord,
    SourceMessageRecord,
)
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.sources.gmail import GmailMessage
from homefinder.sources.policy import SourcePolicy, SourcePolicyRegistry
from homefinder.sources.portal_alerts import (
    GratkaAlertParser,
    MorizonAlertParser,
    OtodomAlertParser,
)

FIXTURES = Path(__file__).parents[2] / "data" / "email_examples"


class FakeGmail:
    def __init__(self, message: GmailMessage) -> None:
        self.message = message
        self.labels: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    def list_messages(self, *, label_id: str, limit: int) -> list[str]:
        return [self.message.provider_message_id]

    def get_message(self, message_id: str) -> GmailMessage:
        return self.message

    def modify_labels(
        self, message_id: str, *, add: tuple[str, ...], remove: tuple[str, ...]
    ) -> None:
        self.labels.append((message_id, add, remove))


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def _count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_multi_listing_message_is_atomic_versioned_and_idempotent(
    session: Session,
) -> None:
    raw = (FIXTURES / "morizon_alert.eml").read_bytes()
    second = b"""
    <article data-listing-id="morizon-example-002">
      <h2 data-field="title">Example second apartment</h2>
      <a data-field="url" href="https://example.com/listings/morizon-example-002">View</a>
      <span data-field="price">900000 PLN</span>
      <span data-field="area">70.5 m2</span>
      <span data-field="rooms">4</span>
      <span data-field="location">Another Example District</span>
    </article>
    """
    raw = raw.replace(b"</body>", second + b"</body>")
    service = AlertIngestionService(
        parser=MorizonAlertParser(),
        catalog=SqlAlchemyCatalogRepository(session),
    )

    first = service.ingest(raw)
    second_result = service.ingest(raw)

    assert first.created is True
    assert second_result.created is False
    assert len(first.preview_htmls) == 2
    assert _count(session, SourceMessageRecord) == 1
    assert _count(session, SourceMessageItemRecord) == 2
    assert _count(session, ListingRecord) == 2
    assert _count(session, ListingSnapshotRecord) == 2
    message = session.scalar(select(SourceMessageRecord))
    assert message is not None
    assert message.parser_version == "sanitized-email-v1"


@pytest.mark.parametrize(
    ("source_key", "parser"),
    (
        ("otodom", OtodomAlertParser()),
        ("morizon", MorizonAlertParser()),
        ("gratka", GratkaAlertParser()),
    ),
)
def test_each_approved_portal_replay_is_idempotent(
    session: Session, source_key: str, parser: OtodomAlertParser
) -> None:
    service = AlertIngestionService(
        parser=parser,
        catalog=SqlAlchemyCatalogRepository(session),
    )
    raw = (FIXTURES / f"{source_key}_alert.eml").read_bytes()

    assert service.ingest(raw).created is True
    assert service.ingest(raw).created is False
    assert _count(session, SourceMessageRecord) == 1
    assert _count(session, ListingRecord) == 1
    assert _count(session, ListingSnapshotRecord) == 1


def test_malformed_multi_listing_message_is_quarantined_without_partial_writes(
    session: Session,
) -> None:
    raw = (
        (FIXTURES / "otodom_alert.eml")
        .read_bytes()
        .replace(
            b"</body>",
            b'<article data-listing-id="broken"><h2 data-field="title">Broken</h2>'
            b"</article></body>",
        )
    )
    message = GmailMessage("otodom-alert-001@example.com", raw)
    gmail = FakeGmail(message)
    policy = SourcePolicy(
        key="otodom",
        allowed_senders=frozenset({"alerts@example.com"}),
        allowed_hosts=frozenset({"example.com"}),
    )
    result = GmailPollingService(
        session=session,
        gmail=gmail,
        ingestion=AlertIngestionService(
            parser=OtodomAlertParser(),
            catalog=SqlAlchemyCatalogRepository(session),
        ),
        policies=SourcePolicyRegistry((policy,)),
        source_key="otodom",
    ).poll()

    assert result.quarantined == 1
    assert _count(session, ListingRecord) == 0
    assert _count(session, ListingSnapshotRecord) == 0
    quarantine = session.get(QuarantinedMessageRecord, message.provider_message_id)
    assert quarantine is not None
    assert quarantine.parser_version == "sanitized-email-v1"
    assert quarantine.reason == (
        "otodom@sanitized-email-v1: required-fields: listing is missing required fields"
    )
    assert "Broken" not in quarantine.reason


def test_portal_contract_policies_never_enable_page_fetching() -> None:
    policy = SourcePolicy(
        key="otodom",
        allowed_senders=frozenset({"alerts@example.com"}),
        allowed_hosts=frozenset({"example.com"}),
    )

    assert policy.page_fetch_enabled is False


def test_reused_message_id_with_changed_content_is_quarantined(
    session: Session,
) -> None:
    raw = (FIXTURES / "otodom_alert.eml").read_bytes()
    message = GmailMessage("otodom-alert-001@example.com", raw)
    gmail = FakeGmail(message)
    policy = SourcePolicy(
        key="otodom",
        allowed_senders=frozenset({"alerts@example.com"}),
        allowed_hosts=frozenset({"example.com"}),
    )

    def polling_service() -> GmailPollingService:
        return GmailPollingService(
            session=session,
            gmail=gmail,
            ingestion=AlertIngestionService(
                parser=OtodomAlertParser(),
                catalog=SqlAlchemyCatalogRepository(session),
            ),
            policies=SourcePolicyRegistry((policy,)),
            source_key="otodom",
        )

    assert polling_service().poll().ingested == 1
    gmail.message = GmailMessage(
        message.provider_message_id,
        raw.replace(b"Example one-room apartment", b"Changed apartment title"),
    )

    assert polling_service().poll().quarantined == 1
    assert _count(session, ListingRecord) == 1
    assert _count(session, ListingSnapshotRecord) == 1
    quarantine = session.get(QuarantinedMessageRecord, message.provider_message_id)
    assert quarantine is not None
    assert "message-conflict" in quarantine.reason
