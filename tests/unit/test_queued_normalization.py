from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def test_normalization_releases_workflow_lease_while_scrape_pending(scrape_queue):
    from homefinder.catalog.orm import (
        CandidateFactSetRecord,
        ListingSnapshotRecord,
        PropertyCandidateRecord,
        WorkflowJobRecord,
    )
    from homefinder.workflow.service import WorkflowService

    repo, snapshots, _, sessions = scrape_queue
    candidate = uuid4()
    with sessions.begin() as session:
        session.add(
            PropertyCandidateRecord(id=candidate, deterministic_key=str(candidate))
        )
        listing_id = session.get(ListingSnapshotRecord, snapshots[0]).listing_id

    def forbidden_fetch(url):
        raise AssertionError("synchronous fallback must not run")

    service = WorkflowService(
        sessions,
        listing_scrapers={"gratka": forbidden_fetch},
        scrape_queue=repo,
        queued_sources=frozenset({"gratka"}),
    )
    job_id = service.jobs.enqueue(
        kind="normalize",
        idempotency_key="synthetic-queued-normalize",
        payload={
            "candidate_id": str(candidate),
            "listing_id": str(listing_id),
            "snapshot_id": str(snapshots[0]),
        },
        available_at=NOW,
    )
    assert service.run_once(worker_id="workflow", now=NOW)
    with sessions() as session:
        job = session.get(WorkflowJobRecord, job_id)
        assert job.state == "retry_wait" and job.lease_token is None
        assert job.last_error_code == "scrape-pending"
        assert session.scalar(select(CandidateFactSetRecord)) is None
    assert len(repo.status(source="gratka").items) == 1
    assert service.run_once(worker_id="workflow", now=NOW + timedelta(seconds=30))
    assert len(repo.status(source="gratka").items) == 1


def test_accepted_partial_capture_resumes_unchanged_fact_flow(scrape_queue):
    from homefinder.catalog.orm import (
        CandidateFactSetRecord,
        ListingSnapshotRecord,
        PropertyCandidateRecord,
    )
    from homefinder.parsers.contracts import PageFacts, ParserResult
    from homefinder.scrape_queue.contracts import CaptureOutcome
    from homefinder.workflow.service import WorkflowService

    repo, snapshots, workers, sessions = scrape_queue
    candidate = uuid4()
    with sessions.begin() as session:
        session.add(
            PropertyCandidateRecord(id=candidate, deterministic_key=str(candidate))
        )
        listing_id = session.get(ListingSnapshotRecord, snapshots[0]).listing_id
    service = WorkflowService(
        sessions, scrape_queue=repo, queued_sources=frozenset({"gratka"})
    )
    service.jobs.enqueue(
        kind="normalize",
        idempotency_key="synthetic-resume",
        payload={
            "candidate_id": str(candidate),
            "listing_id": str(listing_id),
            "snapshot_id": str(snapshots[0]),
        },
        available_at=NOW,
    )
    service.run_once(worker_id="workflow", now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    capture = uuid4()
    repo.complete(
        workers[0],
        lease,
        CaptureOutcome(
            capture,
            NOW,
            "a" * 64,
            10,
            ParserResult(
                capture,
                "a" * 64,
                "synthetic",
                (),
                ("rooms",),
                facts=PageFacts(
                    title="Synthetic page title",
                    price_minor=200,
                    price_per_sqm_minor=12_500,
                ),
            ),
        ),
        now=NOW,
    )
    service.run_once(worker_id="workflow", now=NOW + timedelta(seconds=30))
    with sessions() as session:
        result = session.scalars(select(CandidateFactSetRecord)).one()
        assert '"purchase_price_minor":200' in result.facts_json
        assert '"title":"Synthetic page title"' in result.facts_json
        assert '"rooms":null' in result.facts_json
        assert '"price_per_sqm_minor":12500' in result.facts_json


def test_enabled_queue_does_not_require_legacy_scraper_endpoint(monkeypatch, tmp_path):
    from homefinder import cli
    from homefinder.config import Settings
    from homefinder.sources.policy import SourcePolicy

    settings = Settings(
        _env_file=None,
        concurrent_scraping_enabled=True,
        coordinator_credentials_file=tmp_path / "identities",
        gmail_source_policy_file=tmp_path / "policy",
    )
    monkeypatch.setattr(
        cli,
        "_load_source_policy",
        lambda *args: SourcePolicy(
            key=args[1],
            allowed_senders=frozenset({"sender@example.invalid"}),
            allowed_hosts=frozenset({"example.invalid"}),
            page_fetch_enabled=True,
        ),
    )
    assert cli._remote_scrapers(settings) == {}
    assert cli._queued_sources(settings) == frozenset(
        {"gratka", "morizon", "otodom", "olx"}
    )
