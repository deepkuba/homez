from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    Base,
    FeedbackEventRecord,
    FeedbackTokenRecord,
    ListingRecord,
    ListingSnapshotRecord,
    SourceRecord,
)
from homefinder.config import Settings
from homefinder.web.app import create_app

NOW = datetime(2026, 9, 7, 8, tzinfo=timezone.utc)


def _private(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_offer_browser_is_private_and_filters_by_feedback(tmp_path: Path) -> None:
    database = tmp_path / "offers.sqlite"
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    source_id = uuid4()
    rated_id = uuid4()
    unrated_id = uuid4()
    with sessions() as session:
        session.add(SourceRecord(id=source_id, key="olx", display_name="OLX"))
        _add_listing(session, rated_id, source_id, "Oceniona <script>alert(1)</script>")
        _add_listing(session, unrated_id, source_id, "Bez oceny")
        session.add(
            FeedbackTokenRecord(
                token_hash="a" * 64,
                report_id="report-1",
                listing_id=str(rated_id),
                scope="feedback",
                issued_at=NOW,
                expires_at=NOW,
                used_at=NOW,
            )
        )
        session.add(
            FeedbackEventRecord(
                id=uuid4(),
                token_hash="a" * 64,
                report_id="report-1",
                listing_id=str(rated_id),
                value="dislike",
                reason_code="too_expensive",
                comment="Cena > standard",
                actor_hash="actor",
                recorded_at=NOW,
            )
        )
        session.commit()
    admin = _private(tmp_path / "admin-token", "admin-secret")
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

    unauthorized = client.get("/feedback/offers")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["www-authenticate"].startswith("Basic")
    assert client.get("/feedback/offers", auth=("homez", "wrong")).status_code == 401

    all_offers = client.get("/feedback/offers", auth=("homez", "admin-secret"))
    assert all_offers.status_code == 200
    assert "Oceniona &lt;script&gt;alert(1)&lt;/script&gt;" in all_offers.text
    assert "Cena &gt; standard" in all_offers.text
    assert "Za wysoka cena" in all_offers.text
    assert "Bez oceny" in all_offers.text
    assert f"action='/feedback/offers/{rated_id}'" in all_offers.text

    rejected = client.post(
        f"/feedback/offers/{rated_id}",
        auth=("homez", "admin-secret"),
        data={"value": "like", "csrf_token": "wrong"},
    )
    assert rejected.status_code == 400
    csrf = all_offers.cookies["homefinder_offers_csrf"]
    client.cookies.set("homefinder_offers_csrf", csrf)
    missing_reason = client.post(
        f"/feedback/offers/{rated_id}",
        auth=("homez", "admin-secret"),
        data={"value": "dislike", "csrf_token": csrf},
    )
    assert missing_reason.status_code == 400
    recorded = client.post(
        f"/feedback/offers/{rated_id}",
        auth=("homez", "admin-secret"),
        data={"value": "like", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert recorded.status_code == 303
    assert recorded.headers["location"] == "/feedback/offers?feedback=with_feedback"
    with sessions() as session:
        events = session.query(FeedbackEventRecord).order_by(
            FeedbackEventRecord.recorded_at.desc()
        )
        assert events.count() == 2
        assert events.first().value == "like"
        assert events.first().token_hash is None

    rated = client.get(
        "/feedback/offers?feedback=with_feedback", auth=("homez", "admin-secret")
    )
    assert "Oceniona" in rated.text
    assert "Bez oceny" not in rated.text

    unrated = client.get(
        "/feedback/offers?feedback=without_feedback", auth=("homez", "admin-secret")
    )
    assert "Bez oceny" in unrated.text
    assert "Oceniona" not in unrated.text
    assert (
        client.get(
            "/feedback/offers?page=0", auth=("homez", "admin-secret")
        ).status_code
        == 400
    )
    assert (
        client.get(
            "/feedback/offers?feedback=invalid", auth=("homez", "admin-secret")
        ).status_code
        == 422
    )


def _add_listing(
    session: Session, listing_id: UUID, source_id: UUID, title: str
) -> None:
    session.add(
        ListingRecord(
            id=listing_id,
            source_id=source_id,
            source_listing_id=str(listing_id),
            canonical_url=f"https://www.olx.pl/d/oferta/{listing_id}.html",
            title=title,
        )
    )
    session.add(
        ListingSnapshotRecord(
            id=uuid4(),
            listing_id=listing_id,
            observed_at=NOW,
            price_minor=75_000_000,
            currency="PLN",
            area_sqm=Decimal("54.5"),
            rooms=3,
            availability="available",
            location="Kraków",
            description="",
            content_hash=str(uuid4()).replace("-", ""),
        )
    )
