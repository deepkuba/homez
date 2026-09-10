import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from homefinder.operations.backup import (
    backup_database,
    inspect_backup_retention,
    restore_database,
)

NOW = datetime(2029, 9, 10, tzinfo=timezone.utc)
CAPTURE_NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def test_daily_retention_is_bounded_and_uses_capture_time(scrape_queue) -> None:
    from homefinder.catalog.orm import (
        ListingRetentionTombstoneRecord,
        ListingSnapshotRecord,
        PageCaptureRecord,
        ProductionParserResultRecord,
    )
    from homefinder.operations.retention import ProductionRetentionRepository
    from homefinder.parsers.contracts import PageFacts, ParserResult
    from homefinder.scrape_queue.contracts import CaptureOutcome

    queue, snapshots, workers, sessions = scrape_queue
    captured = []
    for snapshot in snapshots[:2]:
        queue.enqueue(source="gratka", snapshot_id=snapshot, now=CAPTURE_NOW)
        lease = queue.claim(workers[0], now=CAPTURE_NOW)
        capture_id = uuid4()
        body = b"synthetic parser input"
        queue.complete(
            workers[0],
            lease,
            CaptureOutcome(
                capture_id,
                CAPTURE_NOW,
                hashlib.sha256(body).hexdigest(),
                len(body),
                ParserResult(
                    capture_id,
                    "a" * 64,
                    "synthetic",
                    (),
                    (),
                    facts=PageFacts(title="Synthetic retained value"),
                ),
            ),
            now=CAPTURE_NOW,
        )
        captured.append(capture_id)
    with sessions.begin() as session:
        original = session.get(ListingSnapshotRecord, snapshots[0])
        newer_snapshot = uuid4()
        session.add(
            ListingSnapshotRecord(
                id=newer_snapshot,
                listing_id=original.listing_id,
                observed_at=NOW.replace(year=2028),
                price_minor=200,
                currency="PLN",
                availability="active",
                description="Synthetic newer observation",
                content_hash="f" * 64,
            )
        )
    newer_fetch = NOW.replace(year=2028)
    queue.register_worker(workers[0], release_hashes=("a" * 64,), now=newer_fetch)
    queue.enqueue(source="gratka", snapshot_id=newer_snapshot, now=newer_fetch)
    newer_lease = queue.claim(workers[0], now=newer_fetch)
    newer_capture = uuid4()
    queue.complete(
        workers[0],
        newer_lease,
        CaptureOutcome(
            newer_capture,
            newer_fetch,
            hashlib.sha256(b"synthetic newer input").hexdigest(),
            len(b"synthetic newer input"),
            ParserResult(
                newer_capture,
                "a" * 64,
                "synthetic",
                (),
                (),
                facts=PageFacts(title="Synthetic newer value"),
            ),
        ),
        now=newer_fetch,
    )
    repository = ProductionRetentionRepository(sessions)

    first = repository.run_daily(now=NOW, batch_size=1)
    result = repository.run_daily(now=NOW + timedelta(seconds=1), batch_size=1)

    assert result.cutoff == (NOW + timedelta(seconds=1)).replace(year=2027)
    assert first.deleted_results == 1
    assert first.backlog_count == 1
    assert first.healthy is False
    assert result.deleted_results == 1
    assert result.failed_results == 0
    assert result.backlog_count == 0
    assert result.healthy is True
    status = repository.status()
    assert status.healthy is True
    assert status.backlog_count == 0
    with sessions() as session:
        remaining_results = session.scalars(select(ProductionParserResultRecord)).all()
        remaining_captures = session.scalars(select(PageCaptureRecord)).all()
        assert len(remaining_results) == 1
        assert [item.id for item in remaining_captures] == [newer_capture]
        tombstones = session.scalars(select(ListingRetentionTombstoneRecord)).all()
        assert len(tombstones) == 1
        assert all(len(item.canonical_url_hash) == 64 for item in tombstones)
        assert all(item.confirmed_inactive is False for item in tombstones)


def test_database_boundaries_rearm_only_after_seven_good_days(scrape_queue) -> None:
    from homefinder.operations.retention import ProductionRetentionRepository

    _queue, _snapshots, _workers, sessions = scrape_queue
    sent = []
    repository = ProductionRetentionRepository(sessions, notify=sent.append)

    repository.record_database_size(
        now=NOW, size_bytes=10_000_000_001, largest_relations=(("safe_table", 100),)
    )
    repository.record_database_size(
        now=NOW + timedelta(days=1), size_bytes=9_000_000_000
    )
    repository.record_database_size(now=NOW + timedelta(days=2), size_bytes=None)
    for day in range(3, 10):
        repository.record_database_size(
            now=NOW + timedelta(days=day), size_bytes=9_000_000_000
        )
    repository.record_database_size(
        now=NOW + timedelta(days=10), size_bytes=10_000_000_001
    )

    assert [message.kind for message in sent] == ["database-size", "database-size"]
    assert all("listing" not in message.safe_detail.lower() for message in sent)


def test_expired_benchmark_detail_is_erased_but_safe_tombstone_remains(
    scrape_queue,
) -> None:
    from homefinder.catalog.orm import (
        BenchmarkFieldCandidateRecord,
        BenchmarkManifestRecord,
        BenchmarkResultRecord,
        BenchmarkRetentionTombstoneRecord,
        BenchmarkRunRecord,
    )
    from homefinder.operations.retention import ProductionRetentionRepository

    _queue, _snapshots, _workers, sessions = scrape_queue
    with sessions.begin() as session:
        session.add(
            BenchmarkManifestRecord(
                id="manifest-retention",
                source="gratka",
                active_release_hash="a" * 64,
                candidate_release_hash="a" * 64,
                created_at=NOW - timedelta(days=40),
                selection_policy="synthetic",
                entries_json="[]",
                strata_json="{}",
            )
        )
        session.add(
            BenchmarkRunRecord(
                id="run-retention",
                manifest_id="manifest-retention",
                source="gratka",
                candidate_release_hash="a" * 64,
                state="complete",
                total_count=1,
                created_at=NOW - timedelta(days=40),
            )
        )
        session.add(
            BenchmarkResultRecord(
                run_id="run-retention",
                entry_id="artifact-entry",
                input_kind="artifact",
                artifact_id="synthetic-artifact",
                variant="synthetic",
                status="compared",
                active_result_json="encrypted-active-detail",
                candidate_result_json="encrypted-candidate-detail",
                expires_at=NOW,
            )
        )
        session.add(
            BenchmarkFieldCandidateRecord(
                run_id="run-retention",
                entry_id="artifact-entry",
                parser_side="candidate",
                position=0,
                name="title",
                value_json="encrypted-value",
                origin="synthetic",
                locator="synthetic",
                expires_at=NOW,
            )
        )

    result = ProductionRetentionRepository(sessions).run_daily(now=NOW, batch_size=1)

    assert result.deleted_benchmark_results == 1
    with sessions() as session:
        detail = session.get(BenchmarkResultRecord, ("run-retention", "artifact-entry"))
        assert detail.active_result_json is None
        assert detail.candidate_result_json is None
        assert session.scalars(select(BenchmarkFieldCandidateRecord)).all() == []
        tombstone = session.get(
            BenchmarkRetentionTombstoneRecord,
            ("run-retention", "artifact-entry"),
        )
        assert (tombstone.source, tombstone.variant, tombstone.status) == (
            "gratka",
            "synthetic",
            "compared",
        )


def test_retention_failure_reminds_daily_and_recovers(scrape_queue) -> None:
    from homefinder.operations.retention import ProductionRetentionRepository

    _queue, _snapshots, _workers, sessions = scrape_queue
    sent = []
    repository = ProductionRetentionRepository(sessions, notify=sent.append)

    repository.record_retention_health(
        now=NOW, healthy=False, error_code="delete-failed"
    )
    repository.record_retention_health(
        now=NOW + timedelta(hours=23), healthy=False, error_code="delete-failed"
    )
    repository.record_retention_health(
        now=NOW + timedelta(hours=24), healthy=False, error_code="delete-failed"
    )
    repository.record_retention_health(now=NOW + timedelta(hours=25), healthy=True)
    repository.record_retention_health(now=NOW + timedelta(hours=26), healthy=True)

    assert [message.kind for message in sent] == [
        "retention-failure",
        "retention-reminder",
        "retention-recovery",
    ]


def test_failed_measurement_resets_boundary_streak_and_records_failed_run(
    scrape_queue,
) -> None:
    from homefinder.catalog.orm import (
        DatabaseSizeAlertStateRecord,
        RetentionJobRunRecord,
    )
    from homefinder.operations.retention import ProductionRetentionRepository

    _queue, _snapshots, _workers, sessions = scrape_queue
    repository = ProductionRetentionRepository(sessions)
    repository.record_database_size(now=NOW, size_bytes=10_000_000_001)
    repository.record_database_size(
        now=NOW + timedelta(days=1), size_bytes=9_000_000_000
    )

    def fail_measurement(_session):  # type: ignore[no-untyped-def]
        raise RuntimeError("synthetic measurement failure")

    failing = ProductionRetentionRepository(sessions, measure_database=fail_measurement)
    try:
        failing.run_daily(now=NOW + timedelta(days=2))
    except RuntimeError:
        pass
    else:
        raise AssertionError("measurement failure must fail the retention run")

    with sessions() as session:
        state = session.get(DatabaseSizeAlertStateRecord, 10_000_000_000)
        failed_run = session.scalars(
            select(RetentionJobRunRecord).order_by(
                RetentionJobRunRecord.finished_at.desc()
            )
        ).first()
        assert state.below_streak == 0
        assert failed_run.failed_results == 1
        assert failed_run.error_code == "retention-run-failed"


def test_backup_manifest_warning_is_dated_and_non_blocking(tmp_path) -> None:
    source = tmp_path / "database.dump"
    backup = tmp_path / "database.dump.enc"
    source.write_bytes(b"synthetic database")
    key = b"k" * 32
    oldest = NOW - timedelta(days=800)

    backup_database(
        source,
        backup,
        key,
        created_at=NOW,
        oldest_production_fetched_at=oldest,
    )
    warning = inspect_backup_retention(backup, now=NOW)
    calls = []
    restore_database(
        backup,
        key,
        database_url="postgresql://example.invalid/homez",
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        now=NOW,
        warn=lambda message: calls.append(message),
    )

    assert warning.backup_created_at == NOW
    assert warning.oldest_production_fetched_at == oldest
    assert warning.live_retention_cutoff == NOW.replace(year=2027)
    assert warning.blocks_restore is False
    assert len(calls) == 2
