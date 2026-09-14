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
