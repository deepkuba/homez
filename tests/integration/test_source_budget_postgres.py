import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_two_workers_share_one_portal_budget(scrape_queue):
    from homefinder.scrape_queue.budget import SourceBudgetRepository
    from homefinder.scrape_queue.contracts import SourceBudgetPolicy

    queue, snapshots, workers, sessions = scrape_queue
    budget = SourceBudgetRepository(
        sessions,
        policies={
            "gratka": SourceBudgetPolicy(
                minimum_interval=timedelta(seconds=10),
                daily_attempt_limit=1_100,
                daily_success_limit=1_000,
            )
        },
    )
    budget.register_proxy_route(route_id="route-a", now=NOW)
    budget.record_proxy_bytes(
        route_id="route-a", source="gratka", transferred_bytes=899_999_000, now=NOW
    )
    leases = []
    for snapshot, worker in zip(snapshots[:2], workers, strict=True):
        queue.enqueue(source="gratka", snapshot_id=snapshot, now=NOW)
        leases.append(queue.claim(worker, now=NOW))
    barrier = Barrier(2)

    def reserve(index):
        barrier.wait(timeout=10)
        return budget.reserve_start(
            workers[index], leases[index], now=NOW, requested_proxy_bytes=1_000
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        permits = list(pool.map(reserve, range(2)))

    assert sum(permit.granted for permit in permits) == 1
    deferred = next(permit for permit in permits if not permit.granted)
    assert deferred.available_at == NOW + timedelta(seconds=10)
    assert budget.snapshot("gratka", now=NOW).attempt_count == 1
    assert sum(permit.route_class == "proxy" for permit in permits) == 1
    assert budget.proxy_snapshot(now=NOW).allocated_bytes == 900_000_000
