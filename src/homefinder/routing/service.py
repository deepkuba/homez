"""Persistent, quota-aware orchestration of route-provider calls."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import PendingRouteQueryRecord, RouteObservationRecord
from homefinder.domain.routing import RouteObservation, RoutingGoal, TravelMode
from homefinder.operations.health import HealthRegistry, HealthState
from homefinder.routing.quota import QuotaExhausted, QuotaLedger


class RouteDirection(str, Enum):
    MORNING = "morning"
    RETURN = "return"


class RouteTimeSemantics(str, Enum):
    ARRIVAL = "arrival"
    DEPARTURE = "departure"


@dataclass(frozen=True, slots=True)
class Waypoint:
    kind: str
    value: str

    @classmethod
    def address(cls, value: str) -> "Waypoint":
        return cls("address", value)

    @classmethod
    def place_id(cls, value: str) -> "Waypoint":
        return cls("place_id", value)


@dataclass(frozen=True, slots=True)
class RouteQuery:
    provider: str
    origin: Waypoint
    destination: Waypoint
    goal_version: int
    direction: RouteDirection
    mode: TravelMode
    requested_at: datetime
    time_semantics: RouteTimeSemantics

    def replacing(self, **changes: object) -> "RouteQuery":
        return replace(self, **changes)  # type: ignore[arg-type]

    @property
    def cache_key(self) -> str:
        payload = {
            "provider": self.provider,
            "origin": [self.origin.kind, self.origin.value],
            "destination": [self.destination.kind, self.destination.value],
            "goal_version": self.goal_version,
            "direction": self.direction.value,
            "mode": self.mode.value,
            "requested_at": _aware(self.requested_at).isoformat(),
            "time_semantics": self.time_semantics.value,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class RouteProvider(Protocol):
    name: str

    def supports(self, query: RouteQuery) -> bool: ...

    def route(self, query: RouteQuery) -> RouteObservation: ...


class RouteCache:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self, query: RouteQuery) -> RouteObservation | None:
        with self._sessions() as session:
            record = session.get(RouteObservationRecord, query.cache_key)
            if record is None:
                return None
            return RouteObservation(
                mode=TravelMode(record.mode),
                duration_minutes=record.duration_minutes,
                provider=record.provider,
                observed_at=_aware(record.observed_at),
                confidence=float(record.confidence),
                advisories=tuple(json.loads(record.advisories)),
            )

    def put(self, query: RouteQuery, route: RouteObservation) -> None:
        with self._sessions() as session:
            session.merge(
                RouteObservationRecord(
                    cache_key=query.cache_key,
                    origin=json.dumps([query.origin.kind, query.origin.value]),
                    destination=json.dumps(
                        [query.destination.kind, query.destination.value]
                    ),
                    goal_version=query.goal_version,
                    direction=query.direction.value,
                    mode=query.mode.value,
                    requested_at=query.requested_at,
                    time_semantics=query.time_semantics.value,
                    duration_minutes=route.duration_minutes,
                    provider=route.provider,
                    observed_at=route.observed_at,
                    confidence=Decimal(str(route.confidence)),
                    advisories=json.dumps(route.advisories),
                )
            )
            session.execute(
                delete(PendingRouteQueryRecord).where(
                    PendingRouteQueryRecord.cache_key == query.cache_key
                )
            )
            session.commit()

    def mark_pending(
        self, query: RouteQuery, *, queued_at: datetime, reason: str
    ) -> None:
        with self._sessions() as session:
            session.merge(
                PendingRouteQueryRecord(
                    cache_key=query.cache_key,
                    provider=query.provider,
                    queued_at=queued_at,
                    reason=reason[:200],
                )
            )
            session.commit()

    def oldest_pending_at(self) -> datetime | None:
        with self._sessions() as session:
            value = session.scalar(select(func.min(PendingRouteQueryRecord.queued_at)))
            return None if value is None else _aware(value)


class RouteEnricher:
    def __init__(
        self,
        provider: RouteProvider,
        quota: QuotaLedger,
        cache: RouteCache,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._quota = quota
        self._cache = cache
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def enrich(
        self,
        *,
        origin: str,
        goal: RoutingGoal,
        morning_arrival_at: datetime,
        return_departure_at: datetime,
    ) -> tuple[tuple[RouteObservation, ...], tuple[RouteObservation, ...]]:
        destination = Waypoint.place_id(goal.destination.place_id)
        candidate = Waypoint.address(origin)
        plans = (
            (
                RouteDirection.MORNING,
                RouteTimeSemantics.ARRIVAL,
                morning_arrival_at,
                candidate,
                destination,
            ),
            (
                RouteDirection.RETURN,
                RouteTimeSemantics.DEPARTURE,
                return_departure_at,
                destination,
                candidate,
            ),
        )
        results: list[list[RouteObservation]] = [[], []]
        for index, (direction, semantics, requested_at, start, end) in enumerate(plans):
            for mode in sorted(goal.allowed_modes, key=lambda value: value.value):
                query = RouteQuery(
                    provider=self._provider.name,
                    origin=start,
                    destination=end,
                    goal_version=goal.version,
                    direction=direction,
                    mode=mode,
                    requested_at=requested_at,
                    time_semantics=semantics,
                )
                if not self._provider.supports(query):
                    continue
                cached = self._cache.get(query)
                if cached is None:
                    try:
                        self._quota.reserve()
                    except QuotaExhausted as error:
                        self._cache.mark_pending(
                            query, queued_at=self._clock(), reason=str(error)
                        )
                        continue
                    try:
                        cached = self._provider.route(query)
                    except Exception as error:
                        if type(error).__name__ == "ProviderQuotaError":
                            self._quota.trip_provider_quota(self._provider.name)
                            self._cache.mark_pending(
                                query, queued_at=self._clock(), reason="provider quota"
                            )
                            continue
                        raise
                    self._cache.put(query, cached)
                results[index].append(cached)
        return tuple(results[0]), tuple(results[1])


def report_routing_health(
    registry: HealthRegistry,
    ledger: QuotaLedger,
    cache: RouteCache,
    *,
    checked_at: datetime,
) -> None:
    snapshot = ledger.snapshot()
    state = (
        HealthState.FAIL
        if snapshot.provider_blocked or snapshot.remaining == 0
        else HealthState.OK
    )
    registry.update(
        "routing_quota",
        state,
        detail=(
            f"reserved={snapshot.reserved}; remaining={snapshot.remaining}; "
            f"provider_blocked={snapshot.provider_blocked}"
        ),
        checked_at=checked_at,
    )
    registry.set_job("routing", cache.oldest_pending_at())


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
