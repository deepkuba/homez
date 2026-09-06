from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Thread
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import Base, FeedbackEventRecord, FeedbackTokenRecord
from homefinder.config import Settings
from homefinder.digest.feedback import (
    FeedbackError,
    SqlAlchemyFeedbackService,
    TokenStatus,
    private_feedback_url,
)
from homefinder.web.app import create_app

NOW = datetime(2026, 9, 4, 8, tzinfo=timezone.utc)


def _sessions(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'feedback.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _private(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_token_is_hashed_persistent_scoped_and_atomic(tmp_path: Path) -> None:
    sessions = _sessions(tmp_path)
    service = SqlAlchemyFeedbackService(sessions)
    token = service.issue("report-1", "listing-1", now=NOW, ttl=timedelta(days=7))
    with sessions() as session:
        stored = session.scalar(select(FeedbackTokenRecord))
        assert stored is not None
        assert stored.token_hash != token
        assert token not in stored.token_hash
    assert (
        service.inspect(token, report_id="report-1", listing_id="listing-1", now=NOW)
        is TokenStatus.VALID
    )
    assert (
        service.inspect(token, report_id="report-1", listing_id="other", now=NOW)
        is TokenStatus.INVALID_SCOPE
    )

    barrier = Barrier(2)
    outcomes: list[bool] = []

    def consume() -> None:
        barrier.wait()
        try:
            service.record(
                method="POST",
                token=token,
                csrf_token="csrf",  # noqa: S106
                expected_csrf="csrf",
                value="like",
                now=NOW,
                report_id="report-1",
                listing_id="listing-1",
                actor_hash="actor",
            )
        except FeedbackError:
            outcomes.append(False)
        else:
            outcomes.append(True)

    workers = [Thread(target=consume) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert outcomes.count(True) == 1
    with sessions() as session:
        assert len(session.scalars(select(FeedbackEventRecord)).all()) == 1


def test_stable_token_repeats_for_delivery_retry(tmp_path: Path) -> None:
    sessions = _sessions(tmp_path)
    service = SqlAlchemyFeedbackService(sessions)
    key = b"k" * 32

    first = service.issue_stable(
        "report-1",
        "listing-1",
        now=NOW,
        ttl=timedelta(days=7),
        signing_key=key,
    )
    retry = service.issue_stable(
        "report-1",
        "listing-1",
        now=NOW + timedelta(hours=1),
        ttl=timedelta(days=7),
        signing_key=key,
    )

    assert retry == first
    with sessions() as session:
        tokens = session.scalars(select(FeedbackTokenRecord)).all()
        assert len(tokens) == 1
        assert tokens[0].token_hash != first


def test_get_is_side_effect_free_and_post_has_security_controls(
    tmp_path: Path,
) -> None:
    sessions = _sessions(tmp_path)
    service = SqlAlchemyFeedbackService(sessions)
    token = service.issue(
        "report-1", "listing-1", now=datetime.now(timezone.utc), ttl=timedelta(days=7)
    )
    salt = _private(tmp_path / "rate-salt", "rate-salt-secret")
    admin = _private(tmp_path / "admin-token", "admin-secret")
    settings = Settings(
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'feedback.sqlite'}",
        feedback_rate_salt_file=salt,
        admin_bearer_token_file=admin,
        _env_file=None,
    )
    client = TestClient(
        create_app(settings, feedback_service=service), base_url="https://testserver"
    )

    form = client.get("/feedback/report-1/listing-1")
    assert form.status_code == 200
    assert 'name="reason_code"' in form.text
    assert 'value="too_expensive"' in form.text
    assert 'name="comment"' in form.text
    assert token not in form.text
    assert form.headers["referrer-policy"] == "no-referrer"
    assert form.headers["cache-control"] == "no-store"
    assert "Secure" in form.headers["set-cookie"]
    assert (
        service.inspect(
            token,
            report_id="report-1",
            listing_id="listing-1",
            now=datetime.now(timezone.utc),
        )
        is TokenStatus.VALID
    )
    csrf = form.cookies["homefinder_csrf"]
    client.cookies.set("homefinder_csrf", csrf)
    recorded = client.post(
        "/feedback/report-1/listing-1",
        data={"token": token, "csrf_token": csrf, "value": "save"},
    )
    assert recorded.status_code == 200
    repeated = client.post(
        "/feedback/report-1/listing-1",
        data={"token": token, "csrf_token": csrf, "value": "save"},
    )
    assert repeated.status_code == 409

    assert (
        client.post(
            "/corrections/property-1",
            json={
                "field": "floor",
                "value": "2",
                "corrected_by": "buyer",
                "reason": "checked",
            },
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/corrections/property-1",
            headers={"Authorization": "Bearer admin-secret"},
            json={
                "field": "floor",
                "value": "2",
                "corrected_by": "buyer",
                "reason": "checked",
            },
        ).status_code
        == 200
    )


def test_dislike_requires_reason_and_persists_optional_comment(tmp_path: Path) -> None:
    sessions = _sessions(tmp_path)
    service = SqlAlchemyFeedbackService(sessions)
    token = service.issue(
        "report-1", "listing-1", now=datetime.now(timezone.utc), ttl=timedelta(days=7)
    )
    settings = Settings(
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'feedback.sqlite'}",
        _env_file=None,
    )
    client = TestClient(
        create_app(settings, feedback_service=service), base_url="https://testserver"
    )
    form = client.get("/feedback/report-1/listing-1")
    csrf = form.cookies["homefinder_csrf"]
    client.cookies.set("homefinder_csrf", csrf)

    missing_reason = client.post(
        "/feedback/report-1/listing-1",
        data={"token": token, "csrf_token": csrf, "value": "dislike"},
    )
    assert missing_reason.status_code == 400
    assert (
        service.inspect(
            token,
            report_id="report-1",
            listing_id="listing-1",
            now=datetime.now(timezone.utc),
        )
        is TokenStatus.VALID
    )

    recorded = client.post(
        "/feedback/report-1/listing-1",
        data={
            "token": token,
            "csrf_token": csrf,
            "value": "dislike",
            "reason_code": "too_expensive",
            "comment": "Cena nie odpowiada standardowi mieszkania.",
        },
    )
    assert recorded.status_code == 200
    with sessions() as session:
        event = session.scalar(select(FeedbackEventRecord))
        assert event is not None
        assert event.reason_code == "too_expensive"
        assert event.comment == "Cena nie odpowiada standardowi mieszkania."


def test_private_link_keeps_token_in_fragment() -> None:
    url = private_feedback_url(
        "https://feedback.example.invalid",
        report_id="report 1",
        listing_id="listing/1",
        token="private-token",  # noqa: S106
    )

    assert "?" not in url
    assert url.endswith("#private-token")
    assert "/report%201/listing%2F1" in url


def test_feedback_form_escapes_path_and_post_rejects_csrf(tmp_path: Path) -> None:
    sessions = _sessions(tmp_path)
    service = SqlAlchemyFeedbackService(sessions)
    report_id = "<img src=x onerror=alert(1)>"
    token = service.issue(
        report_id,
        "listing-1",
        now=datetime.now(timezone.utc),
        ttl=timedelta(days=7),
    )
    settings = Settings(
        environment="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'feedback.sqlite'}",
        _env_file=None,
    )
    client = TestClient(
        create_app(settings, feedback_service=service), base_url="https://testserver"
    )

    path = f"/feedback/{quote(report_id, safe='')}/listing-1"
    form = client.get(path)
    assert form.status_code == 200
    assert report_id not in form.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in form.text
    rejected = client.post(
        path,
        data={"token": token, "csrf_token": "wrong", "value": "like"},
    )
    assert rejected.status_code == 400
