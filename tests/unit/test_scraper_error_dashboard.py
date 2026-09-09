import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    Base,
    ListingRecord,
    SourceRecord,
    WorkflowJobAttemptRecord,
    WorkflowJobRecord,
)
from homefinder.config import Settings
from homefinder.web.app import create_app
from homefinder.web.scraper_errors import load_scraper_errors, stream_scraper_errors

NOW = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)


def test_scraper_error_dashboard_is_private_recent_first_and_escaped(
    tmp_path: Path,
) -> None:
    sessions, database_url = _database(tmp_path)
    with sessions() as session:
        source_id = uuid4()
        listing_id = uuid4()
        session.add(SourceRecord(id=source_id, key="olx", display_name="OLX"))
        session.add(
            ListingRecord(
                id=listing_id,
                source_id=source_id,
                source_listing_id="private-listing-id",
                canonical_url="https://example.invalid/private-listing-id",
                title="Private listing",
            )
        )
        _add_error(
            session,
            kind="normalize",
            occurred_at=NOW,
            payload={"listing_id": str(listing_id)},
            code="portal-blocked",
            detail="Newest <script>alert(1)</script>",
        )
        _add_error(
            session,
            kind="poll",
            occurred_at=NOW - timedelta(minutes=1),
            payload={"source_key": "morizon"},
            code="mailbox-timeout",
            detail="Older error",
        )
        _add_error(
            session,
            kind="report",
            occurred_at=NOW + timedelta(minutes=1),
            payload={},
            code="unrelated",
            detail="Must not appear",
        )
        session.commit()

    client = _client(tmp_path, database_url)

    assert client.get("/feedback/scraper-errors").status_code == 401
    response = client.get("/feedback/scraper-errors", auth=("homez", "admin-secret"))

    assert response.status_code == 200
    assert "Błędy scraperów" in response.text
    assert response.text.index("Newest") < response.text.index("Older error")
    assert "OLX" in response.text
    assert "morizon" in response.text
    assert "&lt;script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "private-listing-id" not in response.text
    assert "Must not appear" not in response.text
    assert "EventSource" in response.text
    assert "Załaduj starsze" in response.text
    assert "connect-src 'self'" in response.headers["content-security-policy"]


def test_scraper_error_history_uses_stable_cursor_and_stream_reads_newer(
    tmp_path: Path,
) -> None:
    sessions, database_url = _database(tmp_path)
    with sessions() as session:
        for number in range(4):
            _add_error(
                session,
                kind="poll",
                occurred_at=NOW + timedelta(minutes=number),
                payload={"source_key": "otodom"},
                code=f"error-{number}",
                detail=f"Detail {number}",
            )
        session.commit()

    first = load_scraper_errors(sessions, limit=2)
    assert [item.code for item in first.items] == ["error-3", "error-2"]
    assert first.next_cursor is not None
    older = load_scraper_errors(sessions, before=first.next_cursor, limit=2)
    assert [item.code for item in older.items] == ["error-1", "error-0"]

    newer = load_scraper_errors(sessions, after=older.items[0].cursor, limit=10)
    assert [item.code for item in newer.items] == ["error-2", "error-3"]

    client = _client(tmp_path, database_url)
    unauthorized = client.get(
        "/feedback/scraper-errors/history", params={"before": first.next_cursor}
    )
    assert unauthorized.status_code == 401
    history = client.get(
        "/feedback/scraper-errors/history",
        params={"before": first.next_cursor},
        auth=("homez", "admin-secret"),
    )
    assert history.status_code == 200
    assert [item["code"] for item in history.json()["items"]] == [
        "error-1",
        "error-0",
    ]
    invalid = client.get(
        "/feedback/scraper-errors/history",
        params={"before": "not-a-cursor"},
        auth=("homez", "admin-secret"),
    )
    assert invalid.status_code == 400

    stream_unauthorized = client.get(
        "/feedback/scraper-errors/stream", params={"after": older.items[0].cursor}
    )
    assert stream_unauthorized.status_code == 401
    invalid_stream = client.get(
        "/feedback/scraper-errors/stream",
        params={"after": older.items[0].cursor},
        headers={"Last-Event-ID": "not-a-cursor"},
        auth=("homez", "admin-secret"),
    )
    assert invalid_stream.status_code == 400

    async def collect_events() -> list[str]:
        events = stream_scraper_errors(
            sessions, after=older.items[0].cursor, poll_interval=0
        )
        try:
            return [await anext(events), await anext(events)]
        finally:
            await events.aclose()

    streamed = asyncio.run(collect_events())
    assert '"code":"error-2"' in streamed[0]
    assert '"code":"error-3"' in streamed[1]
    assert all("event: scraper-error" in event for event in streamed)


def test_dashboard_starts_with_50_errors_and_offers_older_history(
    tmp_path: Path,
) -> None:
    sessions, database_url = _database(tmp_path)
    with sessions() as session:
        for number in range(52):
            _add_error(
                session,
                kind="poll",
                occurred_at=NOW + timedelta(seconds=number),
                payload={"source_key": "olx"},
                code=f"page-error-{number}",
                detail=f"Page detail {number}",
            )
        session.commit()

    response = _client(tmp_path, database_url).get(
        "/feedback/scraper-errors", auth=("homez", "admin-secret")
    )

    assert response.status_code == 200
    assert response.text.count("data-cursor=") == 50
    assert "page-error-51" in response.text
    assert "page-error-2" in response.text
    assert ">page-error-1</td>" not in response.text
    assert "id=load-more" in response.text
    assert "id=load-more data-before=" in response.text


def test_stream_does_not_drop_errors_across_internal_batches(tmp_path: Path) -> None:
    sessions, _ = _database(tmp_path)
    with sessions() as session:
        _add_error(
            session,
            kind="poll",
            occurred_at=NOW,
            payload={"source_key": "olx"},
            code="stream-anchor",
            detail="Anchor",
        )
        session.commit()
    anchor = load_scraper_errors(sessions, limit=1).items[0].cursor

    with sessions() as session:
        for number in range(105):
            _add_error(
                session,
                kind="normalize",
                occurred_at=NOW + timedelta(seconds=number + 1),
                payload={"source_key": "otodom"},
                code=f"stream-error-{number}",
                detail=f"Stream detail {number}",
            )
        session.commit()

    async def collect_events() -> list[str]:
        events = stream_scraper_errors(sessions, after=anchor, poll_interval=0)
        try:
            return [await anext(events) for _ in range(105)]
        finally:
            await events.aclose()

    streamed = asyncio.run(collect_events())
    assert len(streamed) == 105
    assert '"code":"stream-error-0"' in streamed[0]
    assert '"code":"stream-error-104"' in streamed[-1]


def _database(tmp_path: Path) -> tuple[sessionmaker[Session], str]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'errors.sqlite'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False), database_url


def _client(tmp_path: Path, database_url: str) -> TestClient:
    admin = tmp_path / "admin-token"
    admin.write_text("admin-secret", encoding="utf-8")
    admin.chmod(0o600)
    return TestClient(
        create_app(
            Settings(
                environment="test",
                database_url=database_url,
                admin_bearer_token_file=admin,
                _env_file=None,
            )
        ),
        base_url="https://testserver",
    )


def _add_error(
    session: Session,
    *,
    kind: str,
    occurred_at: datetime,
    payload: dict[str, str],
    code: str,
    detail: str,
) -> None:
    job_id = uuid4()
    lease_token = uuid4()
    session.add(
        WorkflowJobRecord(
            id=job_id,
            kind=kind,
            idempotency_key=f"{kind}:{job_id}",
            payload_json=json.dumps(payload),
            state="retry_wait",
            priority=100,
            available_at=occurred_at,
            attempt_count=1,
            max_attempts=8,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            parent_job_id=None,
            root_job_id=job_id,
            created_at=occurred_at,
            updated_at=occurred_at,
            started_at=occurred_at,
            finished_at=None,
            last_error_code=code,
            last_error_detail=detail,
        )
    )
    session.add(
        WorkflowJobAttemptRecord(
            job_id=job_id,
            attempt_number=1,
            lease_token=lease_token,
            worker_id="test-worker",
            started_at=occurred_at - timedelta(seconds=1),
            finished_at=occurred_at,
            outcome="retry_wait",
            error_code=code,
            error_detail=detail,
        )
    )
