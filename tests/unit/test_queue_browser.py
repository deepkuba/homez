import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    Base,
    ListingRecord,
    SourceRecord,
    WorkflowJobRecord,
)
from homefinder.config import Settings
from homefinder.web.app import create_app

NOW = datetime(2026, 9, 7, 8, tzinfo=timezone.utc)


def test_queue_browser_is_private_and_summarizes_jobs(tmp_path: Path) -> None:
    database = tmp_path / "queue.sqlite"
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    source_id = uuid4()
    listing_id = uuid4()
    with sessions() as session:
        session.add(SourceRecord(id=source_id, key="olx", display_name="OLX"))
        session.add(
            ListingRecord(
                id=listing_id,
                source_id=source_id,
                source_listing_id="listing-1",
                canonical_url="https://www.olx.pl/d/oferta/listing-1.html",
                title="Oferta",
            )
        )
        _add_job(session, "normalize", "pending", listing_id=listing_id)
        _add_job(
            session,
            "normalize",
            "retry_wait",
            listing_id=listing_id,
            error_code="portal-<blocked>",
        )
        _add_job(session, "report", "running")
        _add_job(session, "poll", "succeeded")
        session.commit()

    admin = tmp_path / "admin-token"
    admin.write_text("admin-secret", encoding="utf-8")
    admin.chmod(0o600)
    client = TestClient(
        create_app(
            Settings(
                environment="test",
                database_url=f"sqlite+pysqlite:///{database}",
                admin_bearer_token_file=admin,
                _env_file=None,
            )
        ),
        base_url="https://testserver",
    )

    unauthorized = client.get("/feedback/queue")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["www-authenticate"].startswith("Basic")
    assert client.get("/feedback/queue", auth=("homez", "wrong")).status_code == 401

    response = client.get("/feedback/queue", auth=("homez", "admin-secret"))

    assert response.status_code == 200
    assert "Kolejka Homez" in response.text
    assert "http-equiv=refresh content=15" in response.text
    assert "Oczekujące</span><strong>1</strong>" in response.text
    assert "W toku</span><strong>1</strong>" in response.text
    assert "Ponowienie</span><strong>1</strong>" in response.text
    assert "OLX" in response.text
    assert "portal-&lt;blocked&gt;" in response.text
    assert "href='/feedback/offers'" in response.text
    assert "listing-1" not in response.text
    assert response.headers["cache-control"] == "no-store"


def _add_job(
    session: Session,
    kind: str,
    state: str,
    *,
    listing_id: UUID | None = None,
    error_code: str | None = None,
) -> None:
    job_id = uuid4()
    payload = {"listing_id": str(listing_id)} if listing_id is not None else {}
    session.add(
        WorkflowJobRecord(
            id=job_id,
            kind=kind,
            idempotency_key=f"{kind}:{job_id}",
            payload_json=json.dumps(payload),
            state=state,
            priority=100,
            available_at=NOW,
            attempt_count=1 if state != "pending" else 0,
            max_attempts=8,
            lease_owner="worker" if state == "running" else None,
            lease_token=uuid4() if state == "running" else None,
            lease_expires_at=(
                NOW + timedelta(minutes=5) if state == "running" else None
            ),
            parent_job_id=None,
            root_job_id=job_id,
            created_at=NOW,
            updated_at=NOW,
            started_at=NOW if state != "pending" else None,
            finished_at=NOW if state == "succeeded" else None,
            last_error_code=error_code,
            last_error_detail=None,
        )
    )
