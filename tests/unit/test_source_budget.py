from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def test_budget_policy_validates_reference_profile():
    from homefinder.scrape_queue.contracts import SourceBudgetPolicy

    policy = SourceBudgetPolicy(
        minimum_interval=timedelta(seconds=10),
        daily_attempt_limit=1_100,
        daily_success_limit=1_000,
    )
    assert policy.minimum_interval == timedelta(seconds=10)
    with pytest.raises(ValueError):
        SourceBudgetPolicy(
            minimum_interval=timedelta(0),
            daily_attempt_limit=1_000,
            daily_success_limit=1_000,
        )
    with pytest.raises(ValueError):
        SourceBudgetPolicy(
            minimum_interval=timedelta(seconds=10),
            daily_attempt_limit=999,
            daily_success_limit=1_000,
        )


def test_proxy_ledger_uses_reviewed_billing_cycle_anchor(scrape_queue):
    from sqlalchemy import select

    from homefinder.catalog.orm import ProxyUsageLedgerRecord
    from homefinder.scrape_queue.budget import SourceBudgetRepository
    from homefinder.scrape_queue.contracts import SourceBudgetPolicy

    _, _, _, sessions = scrape_queue
    budget = SourceBudgetRepository(
        sessions,
        policies={"gratka": SourceBudgetPolicy(timedelta(seconds=10), 1100, 1000)},
        billing_cycle_anchor_day=15,
    )
    budget.register_proxy_route(route_id="route-a", now=NOW)
    budget.record_proxy_bytes(
        route_id="route-a", source="gratka", transferred_bytes=10, now=NOW
    )
    next_cycle = NOW + timedelta(days=6)
    budget.record_proxy_bytes(
        route_id="route-a", source="gratka", transferred_bytes=20, now=next_cycle
    )

    with sessions() as session:
        rows = session.scalars(
            select(ProxyUsageLedgerRecord).order_by(
                ProxyUsageLedgerRecord.billing_cycle
            )
        ).all()
    assert [(row.billing_cycle, row.transferred_bytes) for row in rows] == [
        ("2026-08-15", 10),
        ("2026-09-15", 20),
    ]


def test_proxy_billing_cycle_column_holds_anchored_date():
    from homefinder.catalog.orm import ProxyUsageLedgerRecord

    assert ProxyUsageLedgerRecord.__table__.c.billing_cycle.type.length == 10


def test_discovery_capture_reserves_budget_without_active_parser(scrape_queue):
    from homefinder.catalog.orm import PortalParserActivationRecord
    from homefinder.scrape_queue.budget import SourceBudgetRepository
    from homefinder.scrape_queue.contracts import SourceBudgetPolicy, TaskClass

    queue, snapshots, workers, sessions = scrape_queue
    with sessions.begin() as session:
        session.delete(session.get(PortalParserActivationRecord, "gratka"))
    task_id = queue.enqueue(
        source="gratka",
        snapshot_id=snapshots[0],
        now=NOW,
        task_class=TaskClass.DISCOVERY_CAPTURE,
        release_hash="a" * 64,
    )
    lease = queue.claim(
        workers[0], now=NOW, task_classes=(TaskClass.DISCOVERY_CAPTURE,)
    )
    assert lease.task_id == task_id
    budget = SourceBudgetRepository(
        sessions,
        policies={"gratka": SourceBudgetPolicy(timedelta(seconds=10), 1_100, 1_000)},
    )

    permit = budget.reserve_start(workers[0], lease, now=NOW)

    assert permit.granted
    assert permit.route_class == "direct"


def test_webshare_ceiling_reserves_100_mb_and_direct_continues(scrape_queue):
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
    queue.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = queue.claim(workers[0], now=NOW)
    permit = budget.reserve_start(
        workers[0], lease, now=NOW, requested_proxy_bytes=2_000
    )
    assert permit.granted and permit.route_class == "direct"
    snapshot = budget.proxy_snapshot(now=NOW)
    assert snapshot.usable_limit_bytes == 900_000_000
    assert snapshot.reserved_allowance_bytes == 100_000_000


def test_proxy_reservations_cannot_race_past_900_mb(scrape_queue):
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
    permits = [
        budget.reserve_start(
            workers[index],
            leases[index],
            now=NOW + timedelta(seconds=index * 10),
            requested_proxy_bytes=1_000,
        )
        for index in range(2)
    ]
    assert sum(item.route_class == "proxy" for item in permits) == 1
    assert budget.proxy_snapshot(now=NOW).allocated_bytes == 900_000_000


def test_proxy_infrastructure_failure_persists_one_direct_fallback(scrape_queue):
    from homefinder.catalog.orm import ProxyRouteHealthRecord, ScrapeAttemptRecord
    from homefinder.scrape_queue.budget import SourceBudgetRepository
    from homefinder.scrape_queue.contracts import SourceBudgetPolicy
    from homefinder.scraper.denial_policy import ResponseClassification

    queue, snapshots, workers, sessions = scrape_queue
    budget = SourceBudgetRepository(
        sessions,
        policies={"gratka": SourceBudgetPolicy(timedelta(seconds=10), 1100, 1000)},
    )
    budget.register_proxy_route(route_id="route-a", now=NOW)
    queue.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = queue.claim(workers[0], now=NOW)
    permit = budget.reserve_start(
        workers[0], lease, now=NOW, requested_proxy_bytes=2_000
    )

    decision = budget.record_outcome(
        workers[0],
        lease,
        permit,
        ResponseClassification.PROXY_INFRASTRUCTURE_FAILURE,
        now=NOW,
    )
    assert decision.retry_direct_at == NOW
    fallback_lease = queue.claim(workers[0], now=NOW)
    assert fallback_lease is not None
    assert not budget.reserve_start(workers[0], fallback_lease, now=NOW).granted
    direct_at = NOW + timedelta(seconds=10)
    direct = budget.reserve_start(workers[0], fallback_lease, now=direct_at)
    assert direct.granted and direct.route_class == "direct"
    assert budget.reserve_start(workers[0], fallback_lease, now=direct_at) == direct
    with sessions() as session:
        attempts = (
            session.query(ScrapeAttemptRecord).filter_by(task_id=lease.task_id).all()
        )
        route = session.get(ProxyRouteHealthRecord, "route-a")
        assert sum(attempt.network_attempt_count for attempt in attempts) == 2
        assert route is not None and not route.healthy


def test_proxy_denial_waits_and_direct_denial_sets_source_cooldown(scrape_queue):
    from homefinder.catalog.orm import ProxySourceQuarantineRecord
    from homefinder.scrape_queue.budget import SourceBudgetRepository
    from homefinder.scrape_queue.contracts import SourceBudgetPolicy
    from homefinder.scraper.denial_policy import ResponseClassification

    queue, snapshots, workers, sessions = scrape_queue
    budget = SourceBudgetRepository(
        sessions,
        policies={"gratka": SourceBudgetPolicy(timedelta(seconds=10), 1100, 1000)},
    )
    budget.register_proxy_route(route_id="route-a", now=NOW)
    queue.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = queue.claim(workers[0], now=NOW)
    proxy = budget.reserve_start(
        workers[0], lease, now=NOW, requested_proxy_bytes=2_000
    )
    decision = budget.record_outcome(
        workers[0],
        lease,
        proxy,
        ResponseClassification.PORTAL_DENIAL,
        now=NOW,
    )
    retry_at = NOW + timedelta(minutes=15)
    assert decision.retry_direct_at == retry_at
    assert queue.claim(workers[0], now=retry_at - timedelta(microseconds=1)) is None
    queue.register_worker(workers[0], release_hashes=("a" * 64,), now=retry_at)
    fallback_lease = queue.claim(workers[0], now=retry_at)
    assert fallback_lease is not None
    direct = budget.reserve_start(workers[0], fallback_lease, now=retry_at)
    assert direct.granted and direct.route_class == "direct"
    budget.record_outcome(
        workers[0],
        fallback_lease,
        direct,
        ResponseClassification.PORTAL_DENIAL,
        now=retry_at,
    )
    assert budget.snapshot(
        "gratka", now=retry_at
    ).cooldown_until == retry_at + timedelta(hours=6)
    with sessions() as session:
        quarantine = session.get(ProxySourceQuarantineRecord, ("route-a", "gratka"))
        assert quarantine is not None and quarantine.until == retry_at.replace(
            tzinfo=None
        )


def test_proxy_reservation_reconciles_to_compressed_response_bytes(scrape_queue):
    from homefinder.scrape_queue.budget import SourceBudgetRepository
    from homefinder.scrape_queue.contracts import SourceBudgetPolicy
    from homefinder.scraper.denial_policy import ResponseClassification

    queue, snapshots, workers, sessions = scrape_queue
    budget = SourceBudgetRepository(
        sessions,
        policies={"gratka": SourceBudgetPolicy(timedelta(seconds=10), 1100, 1000)},
    )
    budget.register_proxy_route(route_id="route-a", now=NOW)
    queue.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = queue.claim(workers[0], now=NOW)
    permit = budget.reserve_start(
        workers[0], lease, now=NOW, requested_proxy_bytes=2_000
    )
    budget.record_outcome(
        workers[0],
        lease,
        permit,
        ResponseClassification.SUCCESS,
        now=NOW,
        transferred_bytes=750,
    )
    snapshot = budget.proxy_snapshot(now=NOW)
    assert snapshot.transferred_bytes == 750
    assert snapshot.allocated_bytes == 750


def test_cross_source_redirect_creates_one_validated_target_handoff(scrape_queue):
    from homefinder.catalog.orm import RedirectHandoffRecord, ScrapeTaskRecord
    from homefinder.scrape_queue.redirects import RedirectHandoffRepository

    queue, snapshots, workers, sessions = scrape_queue
    queue.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = queue.claim(workers[0], now=NOW)
    redirects = RedirectHandoffRepository(sessions)
    target = "https://www.morizon.pl/oferta/synthetic-mzn12345678"

    first = redirects.handoff(
        workers[0], lease, target_source="morizon", target_url=target, now=NOW
    )
    second = redirects.handoff(
        workers[0], lease, target_source="morizon", target_url=target, now=NOW
    )
    assert first == second
    with sessions() as session:
        rows = session.query(RedirectHandoffRecord).all()
        task = session.get(ScrapeTaskRecord, lease.task_id)
        assert len(rows) == 1
        assert rows[0].state == "pending"
        assert rows[0].target_source == "morizon"
        assert task is not None and task.state == "succeeded"


def test_redirect_handoff_rejects_non_listing_and_same_source(scrape_queue):
    from homefinder.scrape_queue.redirects import RedirectHandoffRepository

    queue, snapshots, workers, sessions = scrape_queue
    queue.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = queue.claim(workers[0], now=NOW)
    redirects = RedirectHandoffRepository(sessions)
    with pytest.raises(ValueError):
        redirects.handoff(
            workers[0],
            lease,
            target_source="morizon",
            target_url="https://www.morizon.pl/",
            now=NOW,
        )
    with pytest.raises(ValueError):
        redirects.handoff(
            workers[0],
            lease,
            target_source="gratka",
            target_url=lease.canonical_url,
            now=NOW,
        )
