from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def test_discovery_capture_can_be_claimed_without_active_parser(scrape_queue):
    from homefinder.catalog.orm import PortalParserActivationRecord
    from homefinder.scrape_queue.contracts import TaskClass

    repo, snapshots, workers, sessions = scrape_queue
    with sessions.begin() as session:
        session.delete(session.get(PortalParserActivationRecord, "gratka"))
    task_id = repo.enqueue(
        source="gratka",
        snapshot_id=snapshots[0],
        now=NOW,
        task_class=TaskClass.DISCOVERY_CAPTURE,
        release_hash="a" * 64,
    )

    lease = repo.claim(workers[0], now=NOW, task_classes=(TaskClass.DISCOVERY_CAPTURE,))

    assert lease.task_id == task_id
    assert lease.task_class is TaskClass.DISCOVERY_CAPTURE
    assert lease.release_hash == "a" * 64
    assert lease.activation_epoch == 1


def test_discovery_canary_is_bounded_previewable_and_audited(scrape_queue):
    from homefinder.catalog.orm import (
        DiscoveryCanaryAuditRecord,
        PortalParserActivationRecord,
        ScrapeTaskRecord,
    )

    repo, _, _, sessions = scrape_queue
    with pytest.raises(ValueError, match="requires no active parser"):
        repo.release_discovery_canary(source="gratka", release_hash="a" * 64, now=NOW)
    with sessions.begin() as session:
        session.delete(session.get(PortalParserActivationRecord, "gratka"))

    preview = repo.release_discovery_canary(
        source="gratka", release_hash="a" * 64, now=NOW
    )
    assert 1 <= preview.selected_count <= 25
    assert (preview.enqueued_count, preview.execute) == (0, False)
    with sessions() as session:
        assert session.scalar(select(DiscoveryCanaryAuditRecord)) is None

    released = repo.release_discovery_canary(
        source="gratka",
        release_hash="a" * 64,
        now=NOW,
        execute=True,
        actor="operator@example.invalid",
    )

    assert released.selected_count == preview.selected_count
    assert (released.enqueued_count, released.execute) == (
        preview.selected_count,
        True,
    )
    with sessions() as session:
        assert (
            len(session.scalars(select(ScrapeTaskRecord)).all())
            == preview.selected_count
        )
        audit = session.scalars(select(DiscoveryCanaryAuditRecord)).one()
        assert audit.actor == "operator@example.invalid"
        assert (audit.selected_count, audit.enqueued_count) == (
            preview.selected_count,
            preview.selected_count,
        )
    with pytest.raises(ValueError, match="between 1 and 25"):
        repo.release_discovery_canary(
            source="gratka", release_hash="a" * 64, now=NOW, limit=26
        )


def test_enqueue_is_idempotent_and_only_advertised_source_is_claimed(scrape_queue):
    from homefinder.scrape_queue.contracts import WorkerIdentity

    repo, snapshots, workers, _ = scrape_queue
    first = repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    assert first == repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    wrong = WorkerIdentity(worker_id="morizon-nas", source="morizon", deployment="nas")
    repo.register_worker(wrong, release_hashes=("b" * 64,), now=NOW)
    assert repo.claim(wrong, now=NOW) is None
    repo.register_worker(workers[0], release_hashes=("b" * 64,), now=NOW)
    assert repo.claim(workers[0], now=NOW) is None
    lease = repo.claim(workers[1], now=NOW)
    assert lease.task_id == first
    assert lease.release_hash == "a" * 64 and lease.activation_epoch == 1


def test_expiry_heartbeat_and_completion_fence_old_tokens(scrape_queue):
    from homefinder.scrape_queue.contracts import LostLease

    repo, snapshots, workers, _ = scrape_queue
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    old = repo.claim(workers[0], now=NOW)
    with pytest.raises(LostLease):
        repo.succeed(workers[1], old, now=NOW)
    renewed = repo.heartbeat(workers[0], old, now=NOW + timedelta(seconds=10))
    assert renewed.lease_expires_at > old.lease_expires_at
    with pytest.raises(LostLease):
        repo.succeed(workers[0], old, now=renewed.lease_expires_at)
    assert repo.reap_expired(now=renewed.lease_expires_at) == 1
    repo.register_worker(
        workers[1], release_hashes=("a" * 64,), now=renewed.lease_expires_at
    )
    new = repo.claim(workers[1], now=renewed.lease_expires_at)
    with pytest.raises(LostLease):
        repo.succeed(workers[0], old, now=renewed.lease_expires_at)
    repo.succeed(workers[1], new, now=renewed.lease_expires_at)
    with pytest.raises(LostLease):
        repo.succeed(workers[1], new, now=renewed.lease_expires_at)


def test_activation_change_fences_all_lease_mutations(scrape_queue):
    from homefinder.catalog.orm import PortalParserActivationRecord
    from homefinder.scrape_queue.contracts import LostLease

    repo, snapshots, workers, sessions = scrape_queue
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    with sessions.begin() as session:
        session.get(PortalParserActivationRecord, "gratka").activation_epoch = 2
    for operation in (
        lambda: repo.succeed(workers[0], lease, now=NOW),
        lambda: repo.fail(workers[0], lease, now=NOW, code="transport-error"),
        lambda: repo.defer(
            workers[0],
            lease,
            now=NOW,
            available_at=NOW + timedelta(minutes=15),
            code="portal-denied",
        ),
        lambda: repo.heartbeat(workers[0], lease, now=NOW),
    ):
        with pytest.raises(LostLease):
            operation()
    repo.reap_expired(now=lease.lease_expires_at)
    repo.register_worker(
        workers[1], release_hashes=("a" * 64,), now=lease.lease_expires_at
    )
    assert repo.claim(workers[1], now=lease.lease_expires_at).activation_epoch == 2


def test_live_priority_class_scope_and_bounded_status(scrape_queue):
    from homefinder.scrape_queue.contracts import TaskClass

    repo, snapshots, workers, _ = scrape_queue
    recovery = repo.enqueue(
        source="gratka",
        snapshot_id=snapshots[0],
        now=NOW,
        task_class=TaskClass.ARTIFACT_RECOVERY,
    )
    live = repo.enqueue(source="gratka", snapshot_id=snapshots[1], now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    assert lease.task_id == live
    assert repo.claim(workers[1], now=NOW, task_classes=(TaskClass.LIVE,)) is None
    assert repo.claim(workers[1], now=NOW).task_id == recovery
    first = repo.status(source="gratka", limit=1)
    second = repo.status(source="gratka", limit=1, before=first.next_cursor)
    assert len(first.items) == len(second.items) == 1
    assert first.items[0].task_id != second.items[0].task_id
    assert second.next_cursor is None
    assert "gratka.pl" not in repr(first)
    with pytest.raises(ValueError):
        repo.status(source="gratka", limit=501)


def test_defer_fail_and_attempt_audit_are_durable(scrape_queue):
    from homefinder.catalog.orm import ScrapeAttemptRecord

    repo, snapshots, workers, sessions = scrape_queue
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    later = NOW + timedelta(minutes=15)
    repo.defer(workers[0], lease, now=NOW, available_at=later, code="portal-denied")
    assert repo.claim(workers[1], now=NOW) is None
    repo.register_worker(workers[1], release_hashes=("a" * 64,), now=later)
    retry = repo.claim(workers[1], now=later)
    assert repo.fail(workers[1], retry, now=later, code="parser-error") == "failed"
    assert repo.claim(workers[1], now=later) is None
    with sessions() as session:
        attempts = session.scalars(
            select(ScrapeAttemptRecord).order_by(ScrapeAttemptRecord.attempt_number)
        ).all()
        assert [a.outcome for a in attempts] == ["deferred", "failed"]
        assert [a.code for a in attempts] == ["portal-denied", "parser-error"]


def test_worker_health_identity_and_renewal_are_bounded(scrape_queue):
    from homefinder.scrape_queue.contracts import LostLease, QueuePolicy

    repo, snapshots, workers, _ = scrape_queue
    with pytest.raises(ValueError):
        QueuePolicy(lease_seconds=10, heartbeat_seconds=10)
    with pytest.raises(ValueError):
        QueuePolicy(max_lease_seconds=10)
    with pytest.raises(ValueError):
        repo.register_worker(
            replace(workers[0], deployment="vps"), release_hashes=("a" * 64,), now=NOW
        )
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    assert repo.claim(workers[0], now=NOW + timedelta(hours=1)) is None
    repo.register_worker(workers[0], release_hashes=("a" * 64,), now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    for seconds in range(30, 600, 30):
        repo.heartbeat(workers[0], lease, now=NOW + timedelta(seconds=seconds))
    with pytest.raises(LostLease):
        repo.heartbeat(workers[0], lease, now=NOW + timedelta(seconds=600))


def test_enqueue_rejects_wrong_source_and_inactive_pointer(scrape_queue):
    from homefinder.catalog.orm import PortalParserActivationRecord

    repo, snapshots, _, sessions = scrape_queue
    with pytest.raises(ValueError):
        repo.enqueue(source="morizon", snapshot_id=snapshots[0], now=NOW)
    with sessions.begin() as session:
        session.delete(session.get(PortalParserActivationRecord, "gratka"))
    with pytest.raises(ValueError, match="active"):
        repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)


def test_queue_migration_expands_without_activating_parser(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    url = f"sqlite:///{tmp_path / 'migration.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "20260909_22")
    command.upgrade(config, "head")
    engine = create_engine(url)
    assert {"scraper_workers", "scrape_attempts", "portal_parser_activations"} <= set(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM portal_parser_activations"))
            == 0
        )
    command.check(config)
    engine.dispose()


def test_epoch_change_does_not_duplicate_live_task(scrape_queue):
    from homefinder.catalog.orm import PortalParserActivationRecord

    repo, snapshots, workers, sessions = scrape_queue
    original = repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    with sessions.begin() as session:
        session.get(PortalParserActivationRecord, "gratka").activation_epoch = 2
    assert repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW) == original
    assert repo.claim(workers[0], now=NOW).activation_epoch == 2


def test_network_recovery_is_held_and_bad_diagnostics_do_not_mutate(scrape_queue):
    from homefinder.scrape_queue.contracts import TaskClass

    repo, snapshots, workers, _ = scrape_queue
    repo.enqueue(
        source="gratka",
        snapshot_id=snapshots[0],
        now=NOW,
        task_class=TaskClass.NETWORK_RECOVERY,
    )
    assert repo.claim(workers[0], now=NOW) is None
    repo.enqueue(source="gratka", snapshot_id=snapshots[1], now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    with pytest.raises(ValueError):
        repo.fail(workers[0], lease, now=NOW, code="raw synthetic content")
    repo.succeed(workers[0], lease, now=NOW)


def test_retry_budget_ends_in_terminal_state(scrape_queue):
    from homefinder.scrape_queue.contracts import QueuePolicy

    repo, snapshots, workers, _ = scrape_queue
    repo.policy = QueuePolicy(max_attempts=2)
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    first = repo.claim(workers[0], now=NOW)
    assert repo.fail(workers[0], first, now=NOW, code="transport-error") == "deferred"
    assert repo.claim(workers[0], now=NOW) is None
    second = repo.claim(workers[0], now=NOW + timedelta(seconds=30))
    assert (
        repo.fail(
            workers[0], second, now=NOW + timedelta(seconds=30), code="transport-error"
        )
        == "failed"
    )
    assert repo.claim(workers[0], now=NOW + timedelta(seconds=60)) is None
