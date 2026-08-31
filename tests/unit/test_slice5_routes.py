from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

import pytest

from homefinder.domain.routing import (
    CommuteStatus,
    Destination,
    RouteObservation,
    RoutingGoal,
    TravelMode,
    evaluate_commute,
)
from homefinder.routing.quota import QuotaExhausted, QuotaLedger
from homefinder.routing.service import RouteEnricher, RouteQuery


def _goal(**changes: object) -> RoutingGoal:
    values: dict[str, object] = {
        "version": 1,
        "destination": Destination("dest-v1", "ul. Podbrzezie 6, Krakow"),
        "max_minutes": 45,
        "allowed_modes": frozenset(
            {TravelMode.DRIVE, TravelMode.TRANSIT, TravelMode.WALK, TravelMode.BICYCLE}
        ),
        "stale_after": timedelta(days=7),
    }
    values.update(changes)
    return RoutingGoal(**values)


def _route(mode: TravelMode, minutes: int, *, at: datetime) -> RouteObservation:
    return RouteObservation(mode, minutes, "fake", at, confidence=1.0)


def test_slowest_of_fastest_morning_and_return_controls_hard_goal() -> None:
    now = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
    result = evaluate_commute(
        _goal(),
        morning=(
            _route(TravelMode.TRANSIT, 40, at=now),
            _route(TravelMode.DRIVE, 51, at=now),
        ),
        return_routes=(
            _route(TravelMode.WALK, 44, at=now),
            _route(TravelMode.DRIVE, 46, at=now),
        ),
        evaluated_at=now,
    )

    assert result.status is CommuteStatus.PASS
    assert result.morning_minutes == 40
    assert result.return_minutes == 44
    assert result.controlling_minutes == 44
    assert result.margin_minutes == 1
    assert result.winning_modes == (TravelMode.TRANSIT, TravelMode.WALK)


def test_stale_or_missing_routes_are_unknown_and_never_pass() -> None:
    now = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
    result = evaluate_commute(
        _goal(),
        morning=(_route(TravelMode.TRANSIT, 30, at=now - timedelta(days=8)),),
        return_routes=(),
        evaluated_at=now,
    )

    assert result.status is CommuteStatus.UNKNOWN
    assert result.margin_minutes is None
    assert "stale" in result.explanation or "missing" in result.explanation


def test_quota_reservation_is_atomic_under_concurrency() -> None:
    ledger = QuotaLedger(allowance=3, safety_ratio=1.0)
    barrier = Barrier(5)
    outcomes: list[bool] = []

    def reserve() -> None:
        barrier.wait()
        try:
            ledger.reserve(1)
        except QuotaExhausted:
            outcomes.append(False)
        else:
            outcomes.append(True)

    threads = [Thread(target=reserve) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(outcomes) == 3
    assert ledger.reserved == 3


def test_provider_quota_trip_blocks_until_reset_and_alerts_once() -> None:
    alerts: list[str] = []
    ledger = QuotaLedger(allowance=10, safety_ratio=1.0, alert=alerts.append)

    ledger.trip_provider_quota("routes.googleapis.com")
    with pytest.raises(QuotaExhausted, match="provider quota"):
        ledger.reserve(1)
    with pytest.raises(QuotaExhausted, match="provider quota"):
        ledger.reserve(1)

    assert len(alerts) == 1
    ledger.reset_provider_quota()
    ledger.reserve(1)


def test_enricher_caches_each_versioned_direction_and_mode_query() -> None:
    class FakeProvider:
        calls: list[RouteQuery] = []

        def route(self, query: RouteQuery) -> RouteObservation:
            self.calls.append(query)
            return _route(query.mode, 30, at=query.departure_at)

    provider = FakeProvider()
    goal = _goal(allowed_modes=frozenset({TravelMode.WALK, TravelMode.DRIVE}))
    enricher = RouteEnricher(provider, QuotaLedger(allowance=10, safety_ratio=1.0))
    at = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)

    enricher.enrich(origin="candidate", goal=goal, morning_at=at, return_at=at)
    enricher.enrich(origin="candidate", goal=goal, morning_at=at, return_at=at)

    assert len(provider.calls) == 4
