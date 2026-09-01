import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import Base
from homefinder.domain.routing import (
    CommuteStatus,
    Destination,
    RouteObservation,
    RoutingGoal,
    TravelMode,
    evaluate_commute,
)
from homefinder.operations.health import HealthRegistry
from homefinder.routing.quota import QuotaExhausted, QuotaLedger
from homefinder.routing.service import (
    RouteCache,
    RouteDirection,
    RouteEnricher,
    RouteQuery,
    RouteTimeSemantics,
    Waypoint,
    report_routing_health,
)
from homefinder.sources.routes import GoogleRoutesProvider

NOW = datetime(2026, 9, 1, 6, tzinfo=timezone.utc)


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


def _route(mode: TravelMode, minutes: int, *, at: datetime = NOW) -> RouteObservation:
    return RouteObservation(mode, minutes, "fake", at, confidence=1.0)


def _database(path: Path) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite+pysqlite:///{path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _ledger(
    sessions: sessionmaker[Session],
    *,
    allowance: int = 10,
    alert=None,  # type: ignore[no-untyped-def]
) -> QuotaLedger:
    return QuotaLedger(
        sessions,
        period="2026-09",
        provider="google_routes",
        billable_unit="compute_routes",
        allowance=allowance,
        safety_ratio=1.0,
        alert=alert,
        clock=lambda: NOW,
    )


def test_slowest_of_fastest_morning_and_return_controls_hard_goal() -> None:
    result = evaluate_commute(
        _goal(),
        morning=(
            _route(TravelMode.TRANSIT, 40),
            _route(TravelMode.DRIVE, 51),
        ),
        return_routes=(
            _route(TravelMode.WALK, 44),
            _route(TravelMode.DRIVE, 46),
        ),
        evaluated_at=NOW,
    )

    assert result.status is CommuteStatus.PASS
    assert result.controlling_minutes == 44
    assert result.margin_minutes == 1
    assert result.winning_modes == (TravelMode.TRANSIT, TravelMode.WALK)


def test_stale_or_missing_routes_are_unknown_while_fresh_modes_remain_usable() -> None:
    stale = evaluate_commute(
        _goal(),
        morning=(_route(TravelMode.TRANSIT, 30, at=NOW - timedelta(days=8)),),
        return_routes=(),
        evaluated_at=NOW,
    )
    fresh = evaluate_commute(
        _goal(),
        morning=(
            _route(TravelMode.TRANSIT, 20, at=NOW - timedelta(days=8)),
            _route(TravelMode.WALK, 40),
        ),
        return_routes=(_route(TravelMode.WALK, 42),),
        evaluated_at=NOW,
    )

    assert stale.status is CommuteStatus.UNKNOWN
    assert stale.margin_minutes is None
    assert fresh.status is CommuteStatus.PASS
    assert fresh.morning_minutes == 40


def test_enricher_models_morning_arrival_and_evening_departure_explicitly(
    tmp_path: Path,
) -> None:
    class FakeProvider:
        name = "fake"
        calls: list[RouteQuery] = []

        def supports(self, query: RouteQuery) -> bool:
            return True

        def route(self, query: RouteQuery) -> RouteObservation:
            self.calls.append(query)
            return _route(query.mode, 30)

    sessions = _database(tmp_path / "routing.sqlite")
    provider = FakeProvider()
    enricher = RouteEnricher(provider, _ledger(sessions), RouteCache(sessions))
    morning = datetime(2026, 9, 2, 8, 30, tzinfo=timezone.utc)
    evening = datetime(2026, 9, 2, 17, 30, tzinfo=timezone.utc)

    enricher.enrich(
        origin="candidate address",
        goal=_goal(allowed_modes=frozenset({TravelMode.TRANSIT})),
        morning_arrival_at=morning,
        return_departure_at=evening,
    )

    morning_query, return_query = provider.calls
    assert morning_query.direction is RouteDirection.MORNING
    assert morning_query.time_semantics is RouteTimeSemantics.ARRIVAL
    assert morning_query.requested_at == morning
    assert morning_query.origin == Waypoint.address("candidate address")
    assert morning_query.destination == Waypoint.place_id("dest-v1")
    assert return_query.direction is RouteDirection.RETURN
    assert return_query.time_semantics is RouteTimeSemantics.DEPARTURE
    assert return_query.requested_at == evening
    assert return_query.origin == Waypoint.place_id("dest-v1")
    assert return_query.destination == Waypoint.address("candidate address")


def test_route_cache_survives_restart_and_key_covers_every_request_dimension(
    tmp_path: Path,
) -> None:
    sessions = _database(tmp_path / "cache.sqlite")
    cache = RouteCache(sessions)
    query = RouteQuery(
        provider="google_routes",
        origin=Waypoint.address("A"),
        destination=Waypoint.place_id("B"),
        goal_version=3,
        direction=RouteDirection.MORNING,
        mode=TravelMode.TRANSIT,
        requested_at=NOW,
        time_semantics=RouteTimeSemantics.ARRIVAL,
    )
    cache.put(query, _route(TravelMode.TRANSIT, 31))

    assert RouteCache(sessions).get(query) == _route(TravelMode.TRANSIT, 31)
    variants = (
        {"provider": "other"},
        {"origin": Waypoint.address("other")},
        {"destination": Waypoint.place_id("other")},
        {"goal_version": 4},
        {"direction": RouteDirection.RETURN},
        {"mode": TravelMode.WALK},
        {"requested_at": NOW + timedelta(minutes=1)},
        {"time_semantics": RouteTimeSemantics.DEPARTURE},
    )
    for changes in variants:
        assert cache.get(query.replacing(**changes)) is None


def test_persistent_quota_and_circuit_survive_restart_and_alert_once(
    tmp_path: Path,
) -> None:
    sessions = _database(tmp_path / "quota.sqlite")
    alerts: list[str] = []
    first = _ledger(sessions, allowance=1, alert=alerts.append)
    first.reserve()

    restarted = _ledger(sessions, allowance=1, alert=alerts.append)
    with pytest.raises(QuotaExhausted, match="safety ceiling"):
        restarted.reserve()
    with pytest.raises(QuotaExhausted, match="safety ceiling"):
        restarted.reserve()
    assert restarted.snapshot().reserved == 1
    assert len(alerts) == 1

    restarted.trip_provider_quota()
    again = _ledger(sessions, allowance=1, alert=alerts.append)
    assert again.snapshot().provider_blocked
    with pytest.raises(QuotaExhausted, match="provider quota"):
        again.reserve()
    assert len(alerts) == 1


def test_quota_blocked_queries_become_pending_and_health_reports_them(
    tmp_path: Path,
) -> None:
    class NeverCalledProvider:
        name = "google_routes"

        def supports(self, query: RouteQuery) -> bool:
            return True

        def route(self, query: RouteQuery) -> RouteObservation:
            raise AssertionError("provider must not be called after quota is blocked")

    sessions = _database(tmp_path / "pending.sqlite")
    ledger = _ledger(sessions, allowance=0)
    cache = RouteCache(sessions)
    enricher = RouteEnricher(NeverCalledProvider(), ledger, cache, clock=lambda: NOW)

    morning, returning = enricher.enrich(
        origin="candidate",
        goal=_goal(allowed_modes=frozenset({TravelMode.TRANSIT})),
        morning_arrival_at=NOW,
        return_departure_at=NOW + timedelta(hours=9),
    )
    evaluation = evaluate_commute(
        _goal(allowed_modes=frozenset({TravelMode.TRANSIT})),
        morning=morning,
        return_routes=returning,
        evaluated_at=NOW,
    )
    registry = HealthRegistry()
    report_routing_health(registry, ledger, cache, checked_at=NOW)
    health = registry.snapshot(now=NOW)

    assert evaluation.status is CommuteStatus.UNKNOWN
    assert cache.oldest_pending_at() == NOW
    assert health.components["routing_quota"].state.value == "fail"
    assert "remaining=0" in health.components["routing_quota"].detail
    assert health.oldest_pending_job == "routing"
    assert health.oldest_pending_at == NOW


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *args):  # type: ignore[no-untyped-def]
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


@pytest.mark.parametrize(
    ("mode", "semantics", "expected_time_field"),
    [
        (TravelMode.TRANSIT, RouteTimeSemantics.ARRIVAL, "arrivalTime"),
        (TravelMode.TRANSIT, RouteTimeSemantics.DEPARTURE, "departureTime"),
        (TravelMode.DRIVE, RouteTimeSemantics.DEPARTURE, "departureTime"),
        (TravelMode.WALK, RouteTimeSemantics.ARRIVAL, None),
        (TravelMode.BICYCLE, RouteTimeSemantics.DEPARTURE, None),
    ],
)
def test_google_provider_request_response_contract_for_enabled_modes(
    monkeypatch: pytest.MonkeyPatch,
    mode: TravelMode,
    semantics: RouteTimeSemantics,
    expected_time_field: str | None,
) -> None:
    requests = []

    def fake_urlopen(request, *, timeout):  # type: ignore[no-untyped-def]
        requests.append((request, timeout))
        return _Response({"routes": [{"duration": "1801s"}]})

    monkeypatch.setattr("homefinder.sources.routes.urlopen", fake_urlopen)
    provider = GoogleRoutesProvider("fake-key", clock=lambda: NOW)
    query = RouteQuery(
        provider=provider.name,
        origin=Waypoint.address("A"),
        destination=Waypoint.place_id("B"),
        goal_version=1,
        direction=RouteDirection.MORNING,
        mode=mode,
        requested_at=NOW + timedelta(days=1),
        time_semantics=semantics,
    )

    observation = provider.route(query)
    payload = json.loads(requests[0][0].data)

    assert payload["travelMode"] == mode.value.upper()
    assert payload["origin"] == {"address": "A"}
    assert payload["destination"] == {"placeId": "B"}
    assert observation.duration_minutes == 31
    assert observation.observed_at == NOW
    if expected_time_field is None:
        assert "arrivalTime" not in payload and "departureTime" not in payload
    else:
        assert expected_time_field in payload
    if mode in {TravelMode.WALK, TravelMode.BICYCLE}:
        assert observation.advisories


def test_google_provider_rejects_unsupported_drive_arrival_without_calling_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "homefinder.sources.routes.urlopen",
        lambda *args, **kwargs: pytest.fail("API must not be called"),
    )
    provider = GoogleRoutesProvider("fake-key", clock=lambda: NOW)
    query = RouteQuery(
        provider=provider.name,
        origin=Waypoint.address("A"),
        destination=Waypoint.place_id("B"),
        goal_version=1,
        direction=RouteDirection.MORNING,
        mode=TravelMode.DRIVE,
        requested_at=NOW,
        time_semantics=RouteTimeSemantics.ARRIVAL,
    )

    assert not provider.supports(query)
