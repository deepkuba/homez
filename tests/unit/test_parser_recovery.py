import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def test_network_recovery_defaults_to_dry_run_and_fixed_batches(scrape_queue) -> None:
    from homefinder.parser_recovery import ParserRecoveryRepository

    _queue, _snapshots, _workers, sessions = scrape_queue
    recovery = ParserRecoveryRepository(sessions)

    preview = recovery.release_network_batch(source="gratka", now=NOW)

    assert preview.dry_run is True
    assert preview.allowed_batches == (50, 150, "remainder")
    assert preview.released == 0

    from homefinder import cli

    args = cli._parser().parse_args(
        ["release-parser-recovery", "--source", "gratka", "--batch", "50"]
    )
    assert args.execute is False


def test_artifact_recovery_uses_only_newest_snapshot_and_deduplicates(
    scrape_queue,
) -> None:
    from homefinder.catalog.orm import (
        ArtifactRecoveryBindingRecord,
        ListingSnapshotRecord,
        PageCaptureRecord,
        ParserReleaseRecord,
        PortalParserActivationRecord,
        ProductionParserResultRecord,
        ScrapeTaskRecord,
    )
    from homefinder.parser_recovery import ParserRecoveryRepository
    from homefinder.parsers.contracts import PageFacts, ParserResult
    from homefinder.scrape_queue.contracts import CaptureOutcome, TaskClass

    queue, snapshots, workers, sessions = scrape_queue
    old_snapshot = snapshots[0]
    with sessions.begin() as session:
        original = session.get(ListingSnapshotRecord, old_snapshot)
        newest_snapshot = uuid4()
        session.add(
            ListingSnapshotRecord(
                id=newest_snapshot,
                listing_id=original.listing_id,
                observed_at=NOW + timedelta(minutes=1),
                price_minor=200,
                currency="PLN",
                availability="active",
                description="Synthetic newest",
                content_hash="f" * 64,
            )
        )
    for index, snapshot in enumerate((old_snapshot, newest_snapshot)):
        queue.enqueue(source="gratka", snapshot_id=snapshot, now=NOW)
        lease = queue.claim(workers[0], now=NOW)
        capture_id = uuid4()
        body = f"synthetic-{index}".encode()
        queue.complete(
            workers[0],
            lease,
            CaptureOutcome(
                capture_id,
                NOW,
                hashlib.sha256(body).hexdigest(),
                len(body),
                ParserResult(
                    capture_id,
                    "a" * 64,
                    "synthetic",
                    (),
                    ("rooms",),
                    facts=PageFacts(),
                ),
                str(uuid4()),
            ),
            now=NOW,
        )
    with sessions.begin() as session:
        session.add(
            ParserReleaseRecord(
                source="gratka", release_hash="c" * 64, created_at=NOW, status="active"
            )
        )
        pointer = session.get(PortalParserActivationRecord, "gratka")
        pointer.release_hash = "c" * 64
        pointer.activation_epoch = 2

    recovery = ParserRecoveryRepository(sessions)
    planned = recovery.plan_artifact_recovery(
        source="gratka", release_hash="c" * 64, activation_epoch=2, now=NOW
    )

    assert len(planned) == 1
    assert (
        recovery.plan_artifact_recovery(
            source="gratka", release_hash="c" * 64, activation_epoch=2, now=NOW
        )
        == ()
    )
    with sessions() as session:
        task = session.get(ScrapeTaskRecord, planned[0])
        binding = session.scalar(select(ArtifactRecoveryBindingRecord))
        assert task.snapshot_id == newest_snapshot
        assert task.task_class == "artifact_recovery"
        assert task.priority == 10
        assert binding.capture_id != old_snapshot
        capture_count = session.query(PageCaptureRecord).count()
        original_expiry = binding.result_expires_at
    queue.register_worker(workers[0], release_hashes=("a" * 64, "c" * 64), now=NOW)
    lease = queue.claim(
        workers[0], now=NOW, task_classes=(TaskClass.ARTIFACT_RECOVERY,)
    )
    replay_input = queue.artifact_replay_input(workers[0], lease, now=NOW)
    queue.complete_artifact_replay(
        workers[0],
        lease,
        ParserResult(
            replay_input.capture_id,
            "c" * 64,
            "synthetic-v2",
            (),
            (),
            facts=PageFacts(title="Recovered"),
        ),
        now=NOW,
    )
    with sessions() as session:
        assert session.query(PageCaptureRecord).count() == capture_count
        recovered = session.scalars(
            select(ProductionParserResultRecord).where(
                ProductionParserResultRecord.release_hash == "c" * 64
            )
        ).one()
        assert recovered.capture_id == replay_input.capture_id
        assert recovered.expires_at == original_expiry

    # A newer catalog observation without a retained capture artifact must not
    # make the planner fall back to older bytes.
    with sessions.begin() as session:
        listing_id = session.get(ListingSnapshotRecord, newest_snapshot).listing_id
        latest_snapshot = uuid4()
        session.add(
            ListingSnapshotRecord(
                id=latest_snapshot,
                listing_id=listing_id,
                observed_at=NOW + timedelta(minutes=2),
                price_minor=300,
                currency="PLN",
                availability="active",
                description="Synthetic latest without capture",
                content_hash="d" * 64,
            )
        )
        session.add(
            ParserReleaseRecord(
                source="gratka", release_hash="d" * 64, created_at=NOW, status="active"
            )
        )
        pointer = session.get(PortalParserActivationRecord, "gratka")
        pointer.release_hash = "d" * 64
        pointer.activation_epoch = 3

    assert (
        recovery.plan_artifact_recovery(
            source="gratka", release_hash="d" * 64, activation_epoch=3, now=NOW
        )
        == ()
    )


def test_968_network_backlog_releases_50_150_then_remainder(scrape_queue) -> None:
    from homefinder.catalog.orm import NetworkRecoveryReleaseRecord, ScrapeTaskRecord
    from homefinder.parser_recovery import ParserRecoveryRepository

    _queue, snapshots, _workers, sessions = scrape_queue
    with sessions.begin() as session:
        for index in range(968):
            session.add(
                ScrapeTaskRecord(
                    id=uuid4(),
                    source="gratka",
                    snapshot_id=snapshots[index % len(snapshots)],
                    canonical_url=(
                        f"https://gratka.pl/nieruchomosci/test/ob/{index + 1}"
                    ),
                    task_class="network_recovery",
                    release_hash="a" * 64,
                    activation_epoch=1,
                    idempotency_key=f"{index:064x}",
                    state="held",
                    priority=20,
                    available_at=NOW,
                    created_at=NOW + timedelta(microseconds=index),
                    attempt_count=0,
                )
            )
    recovery = ParserRecoveryRepository(sessions)
    preview = recovery.release_network_batch(source="gratka", now=NOW)
    assert (preview.eligible, preview.released, preview.dry_run) == (968, 0, True)
    first = recovery.release_network_batch(
        source="gratka", now=NOW, batch=50, execute=True, actor="operator"
    )
    second = recovery.release_network_batch(
        source="gratka", now=NOW, batch=150, execute=True, actor="operator"
    )
    final = recovery.release_network_batch(
        source="gratka", now=NOW, batch="remainder", execute=True, actor="operator"
    )
    assert (first.released, second.released, final.released) == (50, 150, 768)
    with pytest.raises(ValueError, match="once"):
        recovery.release_network_batch(
            source="gratka", now=NOW, batch=50, execute=True, actor="operator"
        )
    with sessions() as session:
        assert session.query(NetworkRecoveryReleaseRecord).count() == 3
        states = dict(
            session.execute(
                select(ScrapeTaskRecord.state, func.count()).group_by(
                    ScrapeTaskRecord.state
                )
            ).all()
        )
        assert states == {"pending": 968}


def test_legacy_jobs_are_superseded_with_one_newest_task_per_listing(
    scrape_queue,
) -> None:
    import json

    from homefinder.catalog.orm import (
        ListingSnapshotRecord,
        ScrapeTaskRecord,
        WorkflowJobAttemptRecord,
        WorkflowJobRecord,
    )
    from homefinder.parser_recovery import ParserRecoveryRepository

    _queue, snapshots, _workers, sessions = scrape_queue
    original = snapshots[0]
    with sessions.begin() as session:
        listing_id = session.get(ListingSnapshotRecord, original).listing_id
        newest = uuid4()
        session.add(
            ListingSnapshotRecord(
                id=newest,
                listing_id=listing_id,
                observed_at=NOW + timedelta(minutes=1),
                price_minor=200,
                currency="PLN",
                availability="active",
                description="Newest synthetic",
                content_hash="e" * 64,
            )
        )
        for index, snapshot_id in enumerate((original, newest)):
            job_id = uuid4()
            lease_token = uuid4()
            session.add(
                WorkflowJobRecord(
                    id=job_id,
                    kind="normalize",
                    idempotency_key=f"legacy-{index}",
                    payload_json=json.dumps({"snapshot_id": str(snapshot_id)}),
                    state="dead_letter" if index else "retry_wait",
                    priority=100,
                    available_at=NOW,
                    attempt_count=1,
                    max_attempts=8,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.add(
                WorkflowJobAttemptRecord(
                    job_id=job_id,
                    attempt_number=1,
                    lease_token=lease_token,
                    worker_id="legacy-worker",
                    started_at=NOW,
                    finished_at=NOW,
                    outcome="dead_letter",
                )
            )

    recovery = ParserRecoveryRepository(sessions)
    assert recovery.supersede_legacy_backlog(source="gratka", now=NOW) == 2
    assert recovery.supersede_legacy_backlog(source="gratka", now=NOW) == 0
    with sessions() as session:
        jobs = session.scalars(select(WorkflowJobRecord)).all()
        attempts = session.scalars(select(WorkflowJobAttemptRecord)).all()
        task = session.scalars(
            select(ScrapeTaskRecord).where(
                ScrapeTaskRecord.task_class == "network_recovery"
            )
        ).one()
        assert {job.state for job in jobs} == {"superseded"}
        assert len(attempts) == 2
        assert task.snapshot_id == newest
        assert task.state == "held"
