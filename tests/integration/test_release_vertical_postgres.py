import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.catalog.orm import ReportItemRecord
from homefinder.catalog.profile_repository import SqlAlchemyBuyerProfileRepository
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.config import Settings
from homefinder.digest.delivery import (
    DeliveryOutbox,
    DeliveryWorker,
    MailAcknowledgement,
    MailTransport,
)
from homefinder.digest.feedback import (
    SqlAlchemyFeedbackService,
    TokenStatus,
    private_feedback_url,
)
from homefinder.domain.profile import BuyerProfile
from homefinder.sources.sample_portal import SamplePortalAlertParser
from homefinder.web.app import create_app
from homefinder.workflow.service import WorkflowService

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")
FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_portal" / "valid_alert.eml"


class _TestInbox(MailTransport):
    def __init__(self) -> None:
        self.html = ""
        self.text = ""

    def send(
        self,
        *,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str,
        idempotency_key: str,
    ) -> MailAcknowledgement:
        assert recipient == "buyer@example.invalid"
        assert subject
        assert idempotency_key.startswith("homez:")
        self.html = html_body
        self.text = text_body
        return MailAcknowledgement("test-inbox-message", NOW)


NOW = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)


@pytest.mark.postgres
@pytest.mark.skipif(POSTGRES_URL is None, reason="TEST_POSTGRES_URL is not configured")
def test_alert_to_test_inbox_to_mobile_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(POSTGRES_URL)
    sessions = sessionmaker(engine, expire_on_commit=False)
    unique = uuid4().hex
    raw = (
        FIXTURE.read_bytes()
        .replace(b"sample-20260830-001", f"release-{unique}".encode())
        .replace(b"sample-krk-001", f"release-listing-{unique}".encode())
    )
    with Session(engine) as session:
        AlertIngestionService(
            parser=SamplePortalAlertParser(),
            catalog=SqlAlchemyCatalogRepository(session),
        ).ingest(raw)
        profile = BuyerProfile(version=1_000_000 + int(unique[:6], 16))
        profiles = SqlAlchemyBuyerProfileRepository(session)
        profiles.add_draft(profile, created_at=NOW)
        profiles.approve(profile.version, approved_by="release-test", approved_at=NOW)

    workflow = WorkflowService(sessions)
    workflow.reconcile_catalog(now=NOW)
    workflow.run_until_idle(worker_id="release-test", now=NOW)
    period = f"T{unique[:7]}"
    report_id = workflow.prepare_report(
        period=period,
        cutoff_at=NOW + timedelta(minutes=1),
        routing_goal_version=1,
        now=NOW + timedelta(minutes=1),
    )
    with sessions() as session:
        item = session.scalar(
            select(ReportItemRecord).where(ReportItemRecord.report_id == report_id)
        )
        assert item is not None
        listing_id = str(item.listing_id)

    feedback = SqlAlchemyFeedbackService(sessions)
    issued: dict[str, str] = {}
    issued_urls: dict[str, str] = {}

    def feedback_links(
        current_report_id: str, now: datetime
    ) -> tuple[tuple[str, str], ...]:
        token = feedback.issue(
            current_report_id,
            listing_id,
            now=now,
            ttl=timedelta(days=7),
        )
        issued[listing_id] = token
        url = private_feedback_url(
            "https://feedback.example.invalid",
            report_id=current_report_id,
            listing_id=listing_id,
            token=token,
        )
        issued_urls[listing_id] = url
        return (
            (
                "exploration-1",
                url,
            ),
        )

    outbox = DeliveryOutbox(sessions)
    outbox.enqueue(
        period=period,
        report_id=str(report_id),
        recipient="buyer@example.invalid",
        render_version="digest-v1",
        now=NOW,
    )
    inbox = _TestInbox()
    assert DeliveryWorker(
        sessions, outbox, inbox, feedback_links=feedback_links
    ).run_once(now=NOW)
    token = issued[listing_id]
    assert token in inbox.html
    assert token not in inbox.text

    salt = tmp_path / "rate-salt"
    salt.write_text("release-test-salt", encoding="utf-8")
    salt.chmod(0o600)
    settings = Settings(
        environment="test",
        database_url=POSTGRES_URL,
        feedback_rate_salt_file=salt,
        _env_file=None,
    )
    client = TestClient(
        create_app(settings, feedback_service=feedback),
        base_url="https://feedback.example.invalid",
    )
    parsed = urlsplit(issued_urls[listing_id])
    form = client.get(parsed.path)
    assert form.status_code == 200
    assert (
        feedback.inspect(
            token,
            report_id=str(report_id),
            listing_id=listing_id,
            now=NOW,
        )
        is TokenStatus.VALID
    )
    csrf = form.cookies["homefinder_csrf"]
    client.cookies.set("homefinder_csrf", csrf)
    recorded = client.post(
        parsed.path,
        data={"token": parsed.fragment, "csrf_token": csrf, "value": "like"},
    )
    assert recorded.status_code == 200
    assert (
        feedback.inspect(
            token,
            report_id=str(report_id),
            listing_id=listing_id,
            now=datetime.now(timezone.utc),
        )
        is TokenStatus.USED
    )
