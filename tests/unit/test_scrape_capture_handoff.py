import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def outcome(lease):
    from homefinder.parsers.contracts import PageFacts, ParserResult
    from homefinder.scrape_queue.contracts import CaptureOutcome

    capture_id = uuid4()
    return CaptureOutcome(
        capture_id,
        NOW,
        hashlib.sha256(b"synthetic").hexdigest(),
        9,
        ParserResult(
            capture_id,
            lease.release_hash,
            "synthetic",
            (),
            ("rooms",),
            facts=PageFacts(title="Synthetic page", price_minor=200),
        ),
    )


def test_capture_and_production_result_commit_atomically_and_idempotently(scrape_queue):
    from homefinder.catalog.orm import PageCaptureRecord, ProductionParserResultRecord

    repo, snapshots, workers, sessions = scrape_queue
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    parsed = outcome(lease)
    repo.complete(workers[0], lease, parsed, now=NOW)
    repo.complete(workers[0], lease, parsed, now=NOW + timedelta(seconds=5))
    with sessions() as session:
        capture = session.scalars(select(PageCaptureRecord)).one()
        result = session.scalars(select(ProductionParserResultRecord)).one()
        assert capture.id == parsed.capture_id
        assert result.capture_id == capture.id
        assert result.expires_at.replace(tzinfo=timezone.utc) == NOW.replace(year=2028)
    assert (
        repo.outcome(source="gratka", snapshot_id=snapshots[0]).facts.price_minor == 200
    )


def test_stale_capture_completion_writes_no_raw_or_facts(scrape_queue):
    from homefinder.catalog.orm import PageCaptureRecord, PortalParserActivationRecord
    from homefinder.scrape_queue.contracts import LostLease

    repo, snapshots, workers, sessions = scrape_queue
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    with sessions.begin() as session:
        session.get(PortalParserActivationRecord, "gratka").activation_epoch = 2
    with pytest.raises(LostLease):
        repo.complete(workers[0], lease, outcome(lease), now=NOW)
    with sessions() as session:
        assert session.scalar(select(PageCaptureRecord)) is None


def test_missing_fields_persist_safe_diagnostic_reference(scrape_queue):
    from dataclasses import replace

    from homefinder.catalog.orm import DiagnosticRunRecord, ScrapeAttemptRecord

    repo, snapshots, workers, sessions = scrape_queue
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    artifact_id = str(uuid4())
    parsed = replace(outcome(lease), artifact_id=artifact_id)
    repo.complete(workers[0], lease, parsed, now=NOW)
    with sessions() as session:
        diagnostic = session.scalars(select(DiagnosticRunRecord)).one()
        attempt = session.get(ScrapeAttemptRecord, (lease.task_id, 1))
        assert diagnostic.artifact_id == artifact_id
        assert diagnostic.missing_fields_json == '["rooms"]'
        assert diagnostic.expires_at.replace(tzinfo=timezone.utc) == NOW + timedelta(
            days=30
        )
        assert attempt is not None and attempt.code == "partial"


def test_capture_handoff_migration_matches_metadata(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'handoff.sqlite'}")
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(f"sqlite:///{tmp_path / 'handoff.sqlite'}")
    assert "production_parser_results" in inspect(engine).get_table_names()
    command.check(Config("alembic.ini"))
    engine.dispose()


def test_expired_capture_result_is_not_effective(scrape_queue):
    repo, snapshots, workers, _ = scrape_queue
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    repo.complete(workers[0], lease, outcome(lease), now=NOW)
    assert (
        repo.outcome(
            source="gratka", snapshot_id=snapshots[0], now=NOW.replace(year=2028)
        )
        is None
    )


def test_network_completion_cannot_masquerade_as_artifact_replay(scrape_queue):
    from dataclasses import replace

    from homefinder.scrape_queue.contracts import LostLease, TaskClass

    repo, snapshots, workers, _ = scrape_queue
    repo.enqueue(
        source="gratka",
        snapshot_id=snapshots[0],
        now=NOW,
        task_class=TaskClass.ARTIFACT_RECOVERY,
    )
    replay = repo.claim(workers[0], now=NOW)
    forged = replace(replay, task_class=TaskClass.LIVE)
    with pytest.raises(LostLease):
        repo.complete(workers[0], forged, outcome(forged), now=NOW)
