import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import Base, DigestDeliveryRecord, ReportDraftRecord
from homefinder.digest.delivery import (
    DeliveryOutbox,
    DeliveryState,
    DeliveryWorker,
    FridayScheduler,
    MailAcknowledgement,
    MailTransport,
    MailtrapApiTransport,
)

NOW = datetime(2026, 9, 4, 8, tzinfo=timezone.utc)


def _sessions(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'delivery.sqlite'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _report(sessions: sessionmaker[Session], *, period: str = "2026-W36") -> str:
    report_id = uuid4()
    with sessions() as session:
        session.add(
            ReportDraftRecord(
                id=report_id,
                report_key=uuid4().hex,
                period=period,
                cutoff_at=NOW,
                buyer_profile_version=1,
                routing_goal_version=1,
                selection_version="test",
                render_version="digest-v1",
                status="prepared",
                html_body="<main>private</main>",
                text_body="safe share text",
                content_hash="a" * 64,
                created_at=NOW,
                prepared_at=NOW,
            )
        )
        session.commit()
    return str(report_id)


class FakeTransport(MailTransport):
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.keys: list[str] = []
        self.html_bodies: list[str] = []
        self.text_bodies: list[str] = []

    def send(
        self,
        *,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str,
        idempotency_key: str,
    ) -> MailAcknowledgement:
        del recipient, subject
        self.keys.append(idempotency_key)
        self.html_bodies.append(html_body)
        self.text_bodies.append(text_body)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("pre-acknowledgement failure")
        return MailAcknowledgement(
            provider_message_id=f"provider-{idempotency_key}",
            acknowledged_at=NOW,
        )


def test_friday_scheduler_handles_dst_and_delayed_recovery() -> None:
    winter = FridayScheduler.scheduled_at("2026-W02")
    summer = FridayScheduler.scheduled_at("2026-W36")

    assert winter.astimezone(timezone.utc).hour == 9
    assert summer.astimezone(timezone.utc).hour == 8
    assert (
        FridayScheduler.most_recent_due_period(
            datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
        )
        == "2026-W36"
    )
    assert (
        FridayScheduler.most_recent_due_period(
            datetime(2026, 9, 4, 7, 59, tzinfo=timezone.utc)
        )
        == "2026-W35"
    )


def test_acknowledged_delivery_is_never_claimed_twice(tmp_path: Path) -> None:
    sessions = _sessions(tmp_path)
    report_id = _report(sessions)
    outbox = DeliveryOutbox(sessions)
    assert outbox.enqueue(
        period="2026-W36",
        report_id=report_id,
        recipient="buyer@example.invalid",
        render_version="digest-v1",
        now=NOW,
    )
    assert not outbox.enqueue(
        period="2026-W36",
        report_id=report_id,
        recipient="buyer@example.invalid",
        render_version="digest-v1",
        now=NOW,
    )
    transport = FakeTransport()
    worker = DeliveryWorker(sessions, outbox, transport)

    assert worker.run_once(now=NOW)
    assert not worker.run_once(now=NOW + timedelta(days=1))
    assert transport.keys == [f"homez:2026-W36:{report_id}"]
    with sessions() as session:
        record = session.get(DigestDeliveryRecord, "2026-W36")
        assert record is not None
        assert record.state == DeliveryState.SENT.value
        assert record.provider_message_id is not None


def test_pre_ack_failure_retries_with_same_provider_idempotency_key(
    tmp_path: Path,
) -> None:
    sessions = _sessions(tmp_path)
    report_id = _report(sessions)
    outbox = DeliveryOutbox(sessions)
    outbox.enqueue(
        period="2026-W36",
        report_id=report_id,
        recipient="buyer@example.invalid",
        render_version="digest-v1",
        now=NOW,
    )
    transport = FakeTransport(fail_once=True)
    worker = DeliveryWorker(sessions, outbox, transport)

    assert worker.run_once(now=NOW)
    assert not worker.run_once(now=NOW + timedelta(seconds=59))
    assert worker.run_once(now=NOW + timedelta(seconds=60))
    assert transport.keys == [
        f"homez:2026-W36:{report_id}",
        f"homez:2026-W36:{report_id}",
    ]


def test_delivery_adds_private_links_only_to_html(tmp_path: Path) -> None:
    sessions = _sessions(tmp_path)
    report_id = _report(sessions)
    outbox = DeliveryOutbox(sessions)
    outbox.enqueue(
        period="2026-W36",
        report_id=report_id,
        recipient="buyer@example.invalid",
        render_version="digest-v1",
        now=NOW,
    )
    transport = FakeTransport()
    worker = DeliveryWorker(
        sessions,
        outbox,
        transport,
        feedback_links=lambda _report_id, _now: (
            ("Compliant home 1", "https://feedback.example/feedback/r/l#secret"),
        ),
    )

    assert worker.run_once(now=NOW)
    assert "Private feedback" in transport.html_bodies[0]
    assert "#secret" in transport.html_bodies[0]
    assert transport.text_bodies == ["safe share text"]


def test_mailtrap_transport_sends_provider_contract_and_returns_ack(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    token = tmp_path / "mail-token"
    token.write_text("provider-secret", encoding="utf-8")
    token.chmod(0o600)
    requests = []

    class Response:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return None

        def read(self, _size: int = -1) -> bytes:
            return b'{"success":true,"message_ids":["provider-123"]}'

    def fake_urlopen(request, *, timeout):  # type: ignore[no-untyped-def]
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr("homefinder.digest.delivery.urlopen", fake_urlopen)
    transport = MailtrapApiTransport(
        endpoint="https://sandbox.api.mailtrap.io/api/send/4015",
        allowed_host="sandbox.api.mailtrap.io",
        token_file=token,
        sender="homefinder@example.invalid",
        clock=lambda: NOW,
    )
    acknowledgement = transport.send(
        recipient="buyer@example.invalid",
        subject="weekly",
        html_body="<p>report</p>",
        text_body="report",
        idempotency_key="stable-report-key",
    )

    assert acknowledgement.provider_message_id == "provider-123"
    payload = json.loads(requests[0][0].data)
    assert payload == {
        "from": {"email": "homefinder@example.invalid"},
        "to": [{"email": "buyer@example.invalid"}],
        "subject": "weekly",
        "html": "<p>report</p>",
        "text": "report",
        "custom_variables": {"homez_delivery_id": "stable-report-key"},
    }
    assert requests[0][0].headers["Authorization"] == "Bearer provider-secret"
    assert requests[0][0].headers["Idempotency-key"] == "stable-report-key"
    assert requests[0][1] == 10.0


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://send.api.mailtrap.io/api/send",
        "https://send.api.mailtrap.io.attacker.example/api/send",
        "https://user:password@send.api.mailtrap.io/api/send",
        "https://send.api.mailtrap.io/api/batch",
        "https://sandbox.api.mailtrap.io/api/send/not-a-number",
        "https://sandbox.api.mailtrap.io/api/send/4015?redirect=bad",
    ),
)
def test_mailtrap_transport_rejects_non_mailtrap_or_unsafe_endpoints(
    tmp_path: Path, endpoint: str
) -> None:
    token = tmp_path / "mail-token"
    token.write_text("provider-secret", encoding="utf-8")
    token.chmod(0o600)

    with pytest.raises(ValueError, match="approved Mailtrap HTTPS endpoint"):
        MailtrapApiTransport(
            endpoint=endpoint,
            allowed_host="send.api.mailtrap.io",
            token_file=token,
            sender="homefinder@example.invalid",
        )


@pytest.mark.parametrize(
    "response",
    (
        b'{"success":false,"message_ids":[]}',
        b'{"success":true,"message_ids":[]}',
        b'{"success":true,"message_ids":["one","two"]}',
        b'{"success":true,"message_ids":[1]}',
        b'{"id":"legacy-shape"}',
    ),
)
def test_mailtrap_transport_rejects_missing_or_ambiguous_acknowledgement(
    tmp_path: Path, monkeypatch, response: bytes
) -> None:  # type: ignore[no-untyped-def]
    token = tmp_path / "mail-token"
    token.write_text("provider-secret", encoding="utf-8")
    token.chmod(0o600)

    class Response:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return None

        def read(self, _size: int = -1) -> bytes:
            return response

    monkeypatch.setattr(
        "homefinder.digest.delivery.urlopen", lambda request, timeout: Response()
    )
    transport = MailtrapApiTransport(
        endpoint="https://send.api.mailtrap.io/api/send",
        allowed_host="send.api.mailtrap.io",
        token_file=token,
        sender="homefinder@example.invalid",
    )

    with pytest.raises(RuntimeError, match="did not acknowledge") as captured:
        transport.send(
            recipient="buyer@example.invalid",
            subject="weekly",
            html_body="<p>report</p>",
            text_body="report",
            idempotency_key="stable-report-key",
        )

    assert "provider-secret" not in str(captured.value)
