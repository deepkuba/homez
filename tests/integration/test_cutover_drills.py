from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


@pytest.mark.parametrize("failed_deployment", ("nas", "vps"))
def test_surviving_deployment_reclaims_expired_peer_work(
    scrape_queue, failed_deployment: str
) -> None:
    """A lost deployment cannot strand work or duplicate an active lease."""
    from homefinder.scrape_queue.contracts import LostLease

    queue, snapshots, workers, _sessions = scrape_queue
    by_deployment = {worker.deployment: worker for worker in workers}
    failed = by_deployment[failed_deployment]
    survivor = by_deployment[{"nas": "vps", "vps": "nas"}[failed_deployment]]

    first_task = queue.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    second_task = queue.enqueue(source="gratka", snapshot_id=snapshots[1], now=NOW)

    abandoned_lease = queue.claim(failed, now=NOW)
    independent_lease = queue.claim(survivor, now=NOW)

    assert {abandoned_lease.task_id, independent_lease.task_id} == {
        first_task,
        second_task,
    }
    assert abandoned_lease.task_id != independent_lease.task_id
    assert queue.claim(survivor, now=NOW) is None

    queue.succeed(survivor, independent_lease, now=NOW)
    failover_at = abandoned_lease.lease_expires_at
    queue.register_worker(survivor, release_hashes=("a" * 64,), now=failover_at)

    recovered_lease = queue.claim(survivor, now=failover_at)

    assert recovered_lease is not None
    assert recovered_lease.task_id == abandoned_lease.task_id
    assert recovered_lease.attempt_number == 2
    with pytest.raises(LostLease):
        queue.succeed(failed, abandoned_lease, now=failover_at)
    queue.succeed(survivor, recovered_lease, now=failover_at)
    assert queue.task_state(abandoned_lease.task_id) == "succeeded"


def test_rollback_fences_candidate_completion_and_restores_queue_progress(scrape_queue):
    from hashlib import sha256
    from uuid import uuid4

    from sqlalchemy import select

    from homefinder.catalog.orm import PageCaptureRecord, PortalParserActivationRecord
    from homefinder.parser_releases import ParserReleaseRepository, ReleaseBuild
    from homefinder.parsers.contracts import PageFacts, ParserResult
    from homefinder.scrape_queue.contracts import CaptureOutcome, LostLease

    queue, snapshots, workers, sessions = scrape_queue
    releases = ParserReleaseRepository(sessions, eligibility=lambda *args: True)
    candidate = releases.register(
        ReleaseBuild(
            source="gratka",
            parser_version="synthetic-candidate",
            git_commit="2" * 40,
            parser_content_hash="3" * 64,
            configuration_hash="4" * 64,
            dependency_lock_hash="5" * 64,
            deployable_digest="sha256:" + "6" * 64,
            qualifying_benchmark_run="synthetic-reviewed-benchmark",
        ),
        now=NOW,
    )
    for worker in workers:
        queue.register_worker(
            worker, release_hashes=("a" * 64, candidate.release_hash), now=NOW
        )
    releases.activate(
        source="gratka",
        release_hash=candidate.release_hash,
        actor="operator@example.test",
        compared_metrics="synthetic review",
        expected_epoch=1,
        now=NOW,
    )
    task_id = queue.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    withdrawn = queue.claim(workers[0], now=NOW)
    assert withdrawn.release_hash == candidate.release_hash
    releases.rollback(
        source="gratka",
        actor="operator@example.test",
        reason="synthetic regression",
        expected_epoch=2,
        now=NOW,
    )

    def outcome(lease, fetched_at):
        capture_id = uuid4()
        return CaptureOutcome(
            capture_id,
            fetched_at,
            sha256(b"synthetic").hexdigest(),
            9,
            ParserResult(
                capture_id,
                lease.release_hash,
                "synthetic",
                (),
                (),
                facts=PageFacts(title="Restored release facts"),
            ),
        )

    with pytest.raises(LostLease):
        queue.complete(workers[0], withdrawn, outcome(withdrawn, NOW), now=NOW)
    with sessions() as session:
        assert session.scalar(select(PageCaptureRecord)) is None
        peer = session.get(PortalParserActivationRecord, "morizon")
        assert (peer.release_hash, peer.activation_epoch) == ("b" * 64, 1)

    recovered_at = withdrawn.lease_expires_at
    queue.register_worker(workers[1], release_hashes=("a" * 64,), now=recovered_at)
    recovered = queue.claim(workers[1], now=recovered_at)
    assert (recovered.task_id, recovered.release_hash, recovered.activation_epoch) == (
        task_id,
        "a" * 64,
        3,
    )
    queue.complete(
        workers[1], recovered, outcome(recovered, recovered_at), now=recovered_at
    )
    assert queue.task_state(task_id) == "succeeded"
    assert (
        queue.outcome(
            source="gratka", snapshot_id=snapshots[0], now=recovered_at
        ).facts.title
        == "Restored release facts"
    )


def test_denial_cooldown_survives_coordinator_restart_and_blocks_peer(scrape_queue):
    from datetime import timedelta

    from homefinder.scrape_queue.budget import SourceBudgetRepository
    from homefinder.scrape_queue.contracts import SourceBudgetPolicy
    from homefinder.scraper.denial_policy import ResponseClassification

    queue, snapshots, workers, sessions = scrape_queue
    policies = {"gratka": SourceBudgetPolicy(timedelta(seconds=10), 1100, 1000)}
    budget = SourceBudgetRepository(sessions, policies=policies)
    budget.register_proxy_route(route_id="synthetic-route", now=NOW)
    queue.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = queue.claim(workers[0], now=NOW)
    proxy = budget.reserve_start(
        workers[0], lease, now=NOW, requested_proxy_bytes=2_000
    )
    assert proxy.route_class == "proxy"
    decision = budget.record_outcome(
        workers[0], lease, proxy, ResponseClassification.PORTAL_DENIAL, now=NOW
    )
    retry_at = NOW + timedelta(minutes=15)
    assert decision.retry_direct_at == retry_at
    assert queue.claim(workers[1], now=retry_at - timedelta(seconds=1)) is None

    budget = SourceBudgetRepository(sessions, policies=policies)
    queue.register_worker(workers[1], release_hashes=("a" * 64,), now=retry_at)
    retry = queue.claim(workers[1], now=retry_at)
    direct = budget.reserve_start(workers[1], retry, now=retry_at)
    assert direct.granted and direct.route_class == "direct"
    budget.record_outcome(
        workers[1], retry, direct, ResponseClassification.PORTAL_DENIAL, now=retry_at
    )
    restarted = SourceBudgetRepository(sessions, policies=policies)
    assert restarted.snapshot("gratka", now=retry_at).cooldown_until == (
        retry_at + timedelta(hours=6)
    )
    queue.enqueue(source="gratka", snapshot_id=snapshots[1], now=retry_at)
    queue.register_worker(workers[0], release_hashes=("a" * 64,), now=retry_at)
    peer_lease = queue.claim(workers[0], now=retry_at)
    permit = restarted.reserve_start(workers[0], peer_lease, now=retry_at)
    assert not permit.granted


def test_restore_drill_preserves_dump_and_warns_before_restore(tmp_path):
    from datetime import timedelta
    from subprocess import CompletedProcess

    from cryptography.exceptions import InvalidTag

    from homefinder.operations.backup import backup_database, restore_database

    original = tmp_path / "synthetic.dump"
    original.write_bytes(b"synthetic custom-format database dump")
    encrypted = tmp_path / "synthetic.dump.enc"
    key = b"s" * 32
    backup_database(
        original,
        encrypted,
        key,
        created_at=NOW,
        oldest_production_fetched_at=NOW - timedelta(days=800),
    )
    events = []

    def restore_runner(command, **kwargs):
        assert events and events[0].blocks_restore is False
        assert command[0] == "pg_restore"
        assert "--clean" in command and "--if-exists" in command
        assert kwargs["check"] is True
        assert kwargs["input"] == original.read_bytes()
        events.append("restored")
        return CompletedProcess(command, 0, b"", b"")

    restore_database(
        encrypted,
        key,
        database_url="postgresql://synthetic.invalid/synthetic",
        runner=restore_runner,
        now=NOW,
        warn=events.append,
    )
    assert len(events) == 2
    assert events[0].live_retention_cutoff == NOW.replace(year=2024)
    damaged = bytearray(encrypted.read_bytes())
    damaged[-1] ^= 1
    encrypted.write_bytes(damaged)
    events.clear()
    with pytest.raises(InvalidTag):
        restore_database(
            encrypted,
            key,
            database_url="postgresql://synthetic.invalid/synthetic",
            runner=restore_runner,
            now=NOW,
            warn=events.append,
        )
    assert len(events) == 1
