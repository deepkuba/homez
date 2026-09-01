import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.catalog.orm import CandidateMatchEvaluationRecord, ReportItemRecord
from homefinder.catalog.profile_repository import SqlAlchemyBuyerProfileRepository
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.domain.profile import BuyerProfile
from homefinder.sources.sample_portal import SamplePortalAlertParser
from homefinder.workflow.repository import WorkflowRepository
from homefinder.workflow.service import WorkflowService

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")
FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_portal" / "valid_alert.eml"


@pytest.mark.postgres
@pytest.mark.skipif(POSTGRES_URL is None, reason="TEST_POSTGRES_URL is not configured")
def test_postgres_alert_to_report_and_job_claim_are_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(POSTGRES_URL)
    sessions = sessionmaker(engine, expire_on_commit=False)
    unique = uuid4().hex
    raw = (
        FIXTURE.read_bytes()
        .replace(b"sample-20260830-001", f"workflow-{unique}".encode())
        .replace(b"sample-krakow-001", f"listing-{unique}".encode())
    )
    now = datetime.now(timezone.utc)
    with sessions() as session:
        AlertIngestionService(
            parser=SamplePortalAlertParser(),
            catalog=SqlAlchemyCatalogRepository(session),
        ).ingest(raw)
        profile = BuyerProfile(version=1_000_000 + int(unique[:6], 16))
        profiles = SqlAlchemyBuyerProfileRepository(session)
        profiles.add_draft(profile, created_at=now)
        profiles.approve(profile.version, approved_by="ci", approved_at=now)

    service = WorkflowService(sessions)
    service.reconcile_catalog(now=now)
    service.run_until_idle(worker_id="integration", now=now)
    report_id = service.prepare_report(
        period=f"T{unique[:7]}",
        cutoff_at=now + timedelta(minutes=1),
        routing_goal_version=1,
        now=now + timedelta(minutes=1),
    )
    assert (
        service.prepare_report(
            period=f"T{unique[:7]}",
            cutoff_at=now + timedelta(minutes=1),
            routing_goal_version=1,
            now=now + timedelta(minutes=1),
        )
        == report_id
    )
    with sessions() as session:
        evaluation = session.scalar(
            select(CandidateMatchEvaluationRecord).where(
                CandidateMatchEvaluationRecord.buyer_profile_version == profile.version
            )
        )
        assert evaluation is not None and not evaluation.eligible
        assert (
            session.scalars(
                select(ReportItemRecord).where(ReportItemRecord.report_id == report_id)
            )
            .one()
            .section
            == "exploration"
        )

    repository = WorkflowRepository(sessions)
    repository.enqueue(
        kind="test-claim",
        idempotency_key=f"claim:{unique}",
        payload={},
        available_at=now,
    )
    barrier = Barrier(2)
    claimed = []

    def claim() -> None:
        barrier.wait()
        claimed.append(repository.claim(worker_id=uuid4().hex, now=now))

    workers = [Thread(target=claim) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert sum(item is not None for item in claimed) == 1
