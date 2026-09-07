import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.catalog.orm import (
    Base,
    CandidateFactSetRecord,
    CandidateMatchEvaluationRecord,
    CandidatePresentationRecord,
    ReportDraftRecord,
    ReportItemRecord,
    WorkflowJobRecord,
)
from homefinder.catalog.profile_repository import SqlAlchemyBuyerProfileRepository
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.domain.profile import BuyerProfile
from homefinder.sources.portal_pages import ScrapedListing
from homefinder.sources.sample_portal import SamplePortalAlertParser
from homefinder.workflow.models import JobState, LostLease
from homefinder.workflow.repository import WorkflowRepository, retry_delay
from homefinder.workflow.service import WorkflowService

NOW = datetime(2026, 9, 4, 8, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_portal" / "valid_alert.eml"


def _sessions(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'workflow.sqlite'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_job_retries_are_durable_and_stale_workers_are_fenced(tmp_path: Path) -> None:
    sessions = _sessions(tmp_path)
    repository = WorkflowRepository(sessions)
    job_id = repository.enqueue(
        kind="normalize",
        idempotency_key="normalize:one:v1",
        payload={"snapshot_id": "one"},
        available_at=NOW,
        max_attempts=3,
    )
    assert (
        repository.enqueue(
            kind="normalize",
            idempotency_key="normalize:one:v1",
            payload={"snapshot_id": "one"},
            available_at=NOW,
        )
        == job_id
    )

    first = repository.claim(
        worker_id="worker-a", now=NOW, lease_for=timedelta(seconds=5)
    )
    assert first is not None
    state = repository.fail(
        first,
        now=NOW,
        code="provider-timeout",
        detail="safe bounded detail",
    )
    assert state is JobState.RETRY_WAIT
    assert repository.claim(worker_id="too-early", now=NOW) is None

    retry_at = NOW + retry_delay(job_id, 1)
    second = repository.claim(worker_id="worker-b", now=retry_at)
    assert second is not None
    repository.reap_expired(now=second.lease_expires_at + timedelta(seconds=1))
    replacement = repository.claim(
        worker_id="worker-c", now=second.lease_expires_at + timedelta(seconds=1)
    )
    assert replacement is not None
    with pytest.raises(LostLease):
        repository.succeed(second, now=NOW + timedelta(hours=1))
    repository.succeed(replacement, now=NOW + timedelta(hours=1))

    with sessions() as session:
        record = session.get(WorkflowJobRecord, job_id)
        assert record is not None
        assert record.state == JobState.SUCCEEDED.value
        assert record.attempt_count == 3


def test_poll_slots_are_idempotent_and_worker_dispatches_configured_source(
    tmp_path: Path,
) -> None:
    sessions = _sessions(tmp_path)
    calls: list[str] = []
    workflow = WorkflowService(
        sessions, pollers={"otodom": lambda: calls.append("otodom")}
    )
    first = workflow.enqueue_poll(source_key="otodom", scheduled_at=NOW)
    second = workflow.enqueue_poll(
        source_key="otodom", scheduled_at=NOW + timedelta(seconds=20)
    )

    assert first == second
    assert workflow.run_once(worker_id="poll-worker", now=NOW)
    assert calls == ["otodom"]


def test_sanitized_alert_reaches_idempotent_unknown_safe_report(
    tmp_path: Path,
) -> None:
    sessions = _sessions(tmp_path)
    with sessions() as session:
        AlertIngestionService(
            parser=SamplePortalAlertParser(),
            catalog=SqlAlchemyCatalogRepository(session),
        ).ingest(FIXTURE.read_bytes())
    with sessions() as session:
        profiles = SqlAlchemyBuyerProfileRepository(session)
        profiles.add_draft(BuyerProfile(), created_at=NOW)
        profiles.approve(1, approved_by="buyer", approved_at=NOW)

    workflow = WorkflowService(sessions)
    assert workflow.reconcile_catalog(now=NOW) == 1
    assert workflow.run_until_idle(worker_id="test-worker", now=NOW) == 3

    cutoff = NOW + timedelta(hours=1)
    first = workflow.prepare_report(
        period="2026-W36",
        cutoff_at=cutoff,
        routing_goal_version=1,
        now=cutoff,
    )
    second = workflow.prepare_report(
        period="2026-W36",
        cutoff_at=cutoff,
        routing_goal_version=1,
        now=cutoff,
    )
    assert first == second

    workflow.reconcile_catalog(now=cutoff)
    workflow.run_until_idle(worker_id="test-worker", now=cutoff)
    with sessions() as session:
        evaluation = session.scalar(select(CandidateMatchEvaluationRecord))
        assert evaluation is not None
        assert not evaluation.eligible
        assert evaluation.contains_unknown_hard_rule
        assert session.scalar(select(func.count(ReportDraftRecord.id))) == 1
        report = session.get(ReportDraftRecord, first)
        assert report is not None
        assert report.render_version == "digest-v2"
        assert "Criteria not met" in report.html_body
        assert 'data-homez-feedback-slot="exploration-1"' in report.html_body
        items = session.scalars(select(ReportItemRecord)).all()
        assert len(items) == 1
        assert items[0].section == "exploration"
        assert session.scalar(select(func.count(CandidatePresentationRecord.id))) == 0
        assert (
            session.scalar(select(func.count(CandidateMatchEvaluationRecord.id))) == 1
        )


def test_normalization_uses_listing_details_scraped_by_separate_process(
    tmp_path: Path,
) -> None:
    sessions = _sessions(tmp_path)
    with sessions() as session:
        AlertIngestionService(
            parser=SamplePortalAlertParser(),
            catalog=SqlAlchemyCatalogRepository(session),
        ).ingest(FIXTURE.read_bytes())
        profiles = SqlAlchemyBuyerProfileRepository(session)
        profiles.add_draft(BuyerProfile(), created_at=NOW)
        profiles.approve(1, approved_by="buyer", approved_at=NOW)
    calls: list[str] = []

    def scrape(url: str) -> ScrapedListing:
        calls.append(url)
        return ScrapedListing(
            source_key="sample_portal",
            source_listing_id="sample-krk-001",
            canonical_url=url,
            title="Pelny tytul ze strony",
            price_minor=987_654_00,
            currency="PLN",
            area_sqm=Decimal("63.25"),
            rooms=3,
            location="Krakow, Bronowice",
            description="Pelny opis ze strony oferty",
            availability="active",
        )

    workflow = WorkflowService(sessions, listing_scrapers={"sample_portal": scrape})
    workflow.reconcile_catalog(now=NOW)
    assert workflow.run_once(worker_id="normalize", now=NOW)

    with sessions() as session:
        facts = session.scalar(select(CandidateFactSetRecord))
        assert facts is not None
        payload = json.loads(facts.facts_json)
        assert payload["title"] == "Pelny tytul ze strony"
        assert payload["purchase_price_minor"] == 987_654_00
        assert payload["area_sqm"] == "63.25"
        assert payload["rooms"] == 3
        assert payload["locality"] == "Krakow, Bronowice"
        assert payload["description"] == "Pelny opis ze strony oferty"
        assert payload["availability"] == "active"
    workflow.run_until_idle(worker_id="match", now=NOW)
    with sessions() as session:
        evaluation = session.scalar(select(CandidateMatchEvaluationRecord))
        assert evaluation is not None
        rules = {
            rule["name"]: rule
            for rule in json.loads(evaluation.explanation_json)["rules"]
        }
        assert rules["price"]["state"] == "fail"
        assert rules["price"]["actual"] == "PLN 987,654.00"
    assert calls == ["https://listings.homez.invalid/sample-krk-001"]
