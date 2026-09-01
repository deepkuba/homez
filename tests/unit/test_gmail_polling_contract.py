from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from homefinder.application.gmail_labels import GmailLabelIds
from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.application.poll_gmail import GmailPollingService
from homefinder.catalog.orm import Base, IngestionStateRecord
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.sources.gmail import GmailMessage
from homefinder.sources.policy import SourcePolicy, SourcePolicyRegistry
from homefinder.sources.sample_portal import MAX_MESSAGE_BYTES, SamplePortalAlertParser

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_portal"
LABELS = GmailLabelIds(
    alert="Label_alert",
    processed="Label_processed",
    quarantine="Label_quarantine",
    retry="Label_retry",
)


class FakeGmail:
    def __init__(self, messages: list[GmailMessage]) -> None:
        self.messages = {message.provider_message_id: message for message in messages}
        self.labels: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
        self.fetches: list[str] = []
        self.fail_fetch = False

    def list_messages(self, *, label_id: str, limit: int) -> list[str]:
        assert label_id == LABELS.alert
        return list(self.messages)[:limit]

    def get_message(self, message_id: str) -> GmailMessage:
        self.fetches.append(message_id)
        if self.fail_fetch:
            raise RuntimeError("transient response containing access-secret")
        return self.messages[message_id]

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


def _service(session: Session, gmail: FakeGmail) -> GmailPollingService:
    policy = SourcePolicy(
        key="sample_portal",
        allowed_senders=frozenset({"alerts@fixtures.homez.invalid"}),
        allowed_hosts=frozenset({"listings.homez.invalid"}),
        max_message_bytes=MAX_MESSAGE_BYTES,
    )
    return GmailPollingService(
        session=session,
        gmail=gmail,
        ingestion=AlertIngestionService(
            parser=SamplePortalAlertParser(),
            catalog=SqlAlchemyCatalogRepository(session),
        ),
        policies=SourcePolicyRegistry((policy,)),
        source_key="sample_portal",
        mailbox_key="primary",
        labels=LABELS,
    )


def test_success_uses_actual_label_ids_and_fetches_message_once(
    session: Session,
) -> None:
    raw = (FIXTURE / "valid_alert.eml").read_bytes()
    message = GmailMessage("gmail-1", raw, (LABELS.alert, "STARRED"))
    gmail = FakeGmail([message])

    result = _service(session, gmail).poll()

    assert result.ingested == 1
    assert gmail.fetches == ["gmail-1"]
    assert gmail.labels == [
        (
            "gmail-1",
            (LABELS.processed,),
            (LABELS.alert, LABELS.retry, LABELS.quarantine),
        )
    ]


def test_malformed_message_has_exact_quarantine_transition(
    session: Session,
) -> None:
    raw = (FIXTURE / "malformed_alert.eml").read_bytes()
    gmail = FakeGmail([GmailMessage("gmail-bad", raw, (LABELS.alert,))])

    result = _service(session, gmail).poll()

    assert result.quarantined == 1
    assert gmail.fetches == ["gmail-bad"]
    assert gmail.labels == [
        (
            "gmail-bad",
            (LABELS.quarantine,),
            (LABELS.alert, LABELS.retry, LABELS.processed),
        )
    ]


def test_transient_fetch_failure_is_retryable_and_health_stays_degraded(
    session: Session,
) -> None:
    raw = (FIXTURE / "valid_alert.eml").read_bytes()
    gmail = FakeGmail([GmailMessage("gmail-1", raw, (LABELS.alert,))])
    gmail.fail_fetch = True

    result = _service(session, gmail).poll()

    assert result.failed == 1
    assert gmail.fetches == ["gmail-1"]
    assert gmail.labels == [
        ("gmail-1", (LABELS.retry,), (LABELS.processed, LABELS.quarantine))
    ]
    state = session.scalar(
        select(IngestionStateRecord).where(
            IngestionStateRecord.source_key == "sample_portal"
        )
    )
    assert state is not None
    assert state.status == "degraded"
    assert state.consecutive_failures == 1
    assert state.last_error == "gmail-message-processing-failed"
    assert "access-secret" not in state.last_error


def test_message_without_configured_alert_label_is_not_modified(
    session: Session,
) -> None:
    raw = (FIXTURE / "valid_alert.eml").read_bytes()
    gmail = FakeGmail([GmailMessage("gmail-1", raw, ("INBOX",))])

    result = _service(session, gmail).poll()

    assert result.failed == 1
    assert gmail.fetches == ["gmail-1"]
    assert gmail.labels == []


def test_policy_sender_and_size_are_enforced_before_parser(
    session: Session,
) -> None:
    raw = (
        (FIXTURE / "valid_alert.eml")
        .read_bytes()
        .replace(b"alerts@fixtures.homez.invalid", b"attacker@example.invalid")
    )
    gmail = FakeGmail([GmailMessage("gmail-1", raw, (LABELS.alert,))])

    result = _service(session, gmail).poll()

    assert result.quarantined == 1
    assert gmail.fetches == ["gmail-1"]


def test_source_policy_must_match_parser_sender_and_size(session: Session) -> None:
    gmail = FakeGmail([])
    parser = SamplePortalAlertParser()
    ingestion = AlertIngestionService(
        parser=parser,
        catalog=SqlAlchemyCatalogRepository(session),
    )
    mismatched = SourcePolicy(
        key="sample_portal",
        allowed_senders=frozenset({"other@example.invalid"}),
        allowed_hosts=frozenset({"listings.homez.invalid"}),
        max_message_bytes=MAX_MESSAGE_BYTES - 1,
    )

    with pytest.raises(ValueError, match="policy and parser"):
        GmailPollingService(
            session=session,
            gmail=gmail,
            ingestion=ingestion,
            policies=SourcePolicyRegistry((mismatched,)),
            source_key="sample_portal",
            mailbox_key="primary",
            labels=LABELS,
        )
