import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_nas_and_vps_claim_distinct_tasks(scrape_queue) -> None:
    from homefinder.scrape_queue.contracts import LostLease

    repository, snapshots, workers, _sessions = scrape_queue
    for snapshot in snapshots:
        repository.enqueue(source="gratka", snapshot_id=snapshot, now=NOW)
    barrier = Barrier(2)

    def claim(worker):
        barrier.wait(timeout=10)
        return repository.claim(worker, now=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(claim, workers))
    assert all(lease is not None for lease in leases)
    assert len({lease.task_id for lease in leases}) == 2
    for worker, lease in zip(workers, leases, strict=True):
        repository.succeed(worker, lease, now=NOW)
        with pytest.raises(LostLease):
            repository.succeed(worker, lease, now=NOW)


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_concurrent_enqueue_creates_one_task(scrape_queue):
    repo, snapshots, _, _ = scrape_queue
    barrier = Barrier(4)

    def enqueue(_):
        barrier.wait(timeout=10)
        return repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(enqueue, range(4)))
    assert len(set(ids)) == 1


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_only_one_simultaneous_acknowledgement_commits(scrape_queue):
    from sqlalchemy import select

    from homefinder.catalog.orm import ScrapeAttemptRecord
    from homefinder.scrape_queue.contracts import LostLease

    repo, snapshots, workers, sessions = scrape_queue
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    barrier = Barrier(2)

    def finish(_):
        barrier.wait(timeout=10)
        try:
            repo.succeed(workers[0], lease, now=NOW, response_bytes=100)
            return True
        except LostLease:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(finish, range(2))) == 1
    with sessions() as session:
        attempt = session.scalars(select(ScrapeAttemptRecord)).one()
        assert attempt.outcome == "succeeded" and attempt.response_bytes == 100


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_postgres_expiry_and_epoch_fence_reject_late_results(scrape_queue):
    from homefinder.catalog.orm import PortalParserActivationRecord
    from homefinder.scrape_queue.contracts import LostLease

    repo, snapshots, workers, sessions = scrape_queue
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    old = repo.claim(workers[0], now=NOW)
    with sessions.begin() as session:
        session.get(PortalParserActivationRecord, "gratka").activation_epoch = 2
    with pytest.raises(LostLease):
        repo.succeed(workers[0], old, now=NOW)
    with pytest.raises(LostLease):
        repo.heartbeat(workers[0], old, now=NOW)
    replacement = repo.claim(workers[1], now=old.lease_expires_at)
    assert replacement.task_id == old.task_id and replacement.activation_epoch == 2
    with pytest.raises(LostLease):
        repo.succeed(workers[0], old, now=old.lease_expires_at)
    repo.succeed(workers[1], replacement, now=old.lease_expires_at)


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_result_waiting_on_row_lock_cannot_use_old_request_time(scrape_queue):
    from threading import Event

    from sqlalchemy import select

    from homefinder.catalog.orm import ScrapeTaskRecord
    from homefinder.scrape_queue.contracts import LostLease

    repo, snapshots, workers, sessions = scrape_queue
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = repo.claim(workers[0], now=NOW)
    current = [NOW]
    timed_repo = repo.with_clock(lambda: current[0])
    started = Event()

    def finish():
        started.set()
        with pytest.raises(LostLease):
            timed_repo.succeed(workers[0], lease, now=NOW)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with sessions.begin() as session:
            session.scalar(
                select(ScrapeTaskRecord)
                .where(ScrapeTaskRecord.id == lease.task_id)
                .with_for_update()
            )
            result = pool.submit(finish)
            assert started.wait(timeout=5)
            current[0] = lease.lease_expires_at
        result.result(timeout=5)
