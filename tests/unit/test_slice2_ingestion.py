from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.application.poll_gmail import GmailPollingService
from homefinder.catalog.orm import Base, IngestionStateRecord, QuarantinedMessageRecord
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.sources.gmail import EncryptedTokenStore, GmailMessage
from homefinder.sources.policy import SourcePolicy, SourcePolicyRegistry
from homefinder.sources.sample_portal import SamplePortalAlertParser

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_portal"


class FakeGmail:
    def __init__(self, messages: list[GmailMessage]) -> None:
        self.messages = {message.provider_message_id: message for message in messages}
        self.labels: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    def list_messages(self, *, label_id: str, limit: int) -> list[str]:
        return list(self.messages)[:limit]

    def get_message(self, message_id: str) -> GmailMessage:
        return self.messages[message_id]

    def modify_labels(
        self, message_id: str, *, add: tuple[str, ...], remove: tuple[str, ...]
    ) -> None:
        self.labels.append((message_id, add, remove))


def _service(session: Session, gmail: FakeGmail) -> GmailPollingService:
    return GmailPollingService(
        session=session,
        gmail=gmail,
        ingestion=AlertIngestionService(
            parser=SamplePortalAlertParser(),
            catalog=SqlAlchemyCatalogRepository(session),
        ),
        policies=SourcePolicyRegistry(
            (
                SourcePolicy(
                    key="sample_portal",
                    allowed_senders=frozenset({"alerts@fixtures.homez.invalid"}),
                    allowed_hosts=frozenset({"listings.homez.invalid"}),
                ),
            )
        ),
        source_key="sample_portal",
    )


def test_gmail_poll_is_idempotent_and_marks_processed() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    raw = (FIXTURE / "valid_alert.eml").read_bytes()
    message = GmailMessage("sample-20260830-001@fixtures.homez.invalid", raw)
    with Session(engine) as session:
        gmail = FakeGmail([message])
        first = _service(session, gmail).poll()
        # A real label change removes this; repeat simulates a provider retry.
        second = _service(session, gmail).poll()

        assert (first.ingested, first.duplicates) == (1, 0)
        assert (second.ingested, second.duplicates) == (0, 1)
        assert gmail.labels == [
            (message.provider_message_id, ("HOMEZ_PROCESSED",), ("INBOX",)),
            (message.provider_message_id, ("HOMEZ_PROCESSED",), ("INBOX",)),
        ]
        assert (
            session.scalar(
                select(IngestionStateRecord).where(
                    IngestionStateRecord.source_key == "sample_portal"
                )
            ).last_error
            is None
        )


def test_malformed_gmail_message_is_quarantined_and_not_processed() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    raw = (FIXTURE / "malformed_alert.eml").read_bytes()
    message = GmailMessage("bad-message", raw)
    with Session(engine) as session:
        gmail = FakeGmail([message])
        result = _service(session, gmail).poll()

        quarantine = session.get(QuarantinedMessageRecord, "bad-message")
        assert result.quarantined == 1
        assert quarantine is not None
        assert quarantine.raw_message == raw
        assert gmail.labels == [("bad-message", ("HOMEZ_QUARANTINE",), ("INBOX",))]

        repeated = _service(session, gmail).poll()
        assert repeated.quarantined == 1
        assert session.query(QuarantinedMessageRecord).count() == 1


def test_oauth_token_is_encrypted_at_rest(tmp_path: Path) -> None:
    path = tmp_path / "oauth.json"
    store = EncryptedTokenStore(b"0" * 32)
    store.save(path, {"access_token": "secret-token", "refresh_token": "refresh"})

    assert "secret-token" not in path.read_text()
    assert store.load(path)["access_token"] == "secret-token"


def test_source_policy_denies_page_fetch_by_default() -> None:
    policy = SourcePolicy(
        key="portal",
        allowed_senders=frozenset({"alerts@portal.example"}),
        allowed_hosts=frozenset({"portal.example"}),
    )
    registry = SourcePolicyRegistry((policy,))

    assert registry.can_fetch_pages("portal", "https://portal.example/listing") is False
    assert registry.can_fetch_pages("portal", "http://portal.example/listing") is False
