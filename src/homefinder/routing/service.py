"""Cached, quota-aware orchestration of route-provider calls."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from homefinder.domain.routing import RouteObservation, RoutingGoal, TravelMode
from homefinder.routing.quota import QuotaLedger


@dataclass(frozen=True, slots=True)
class RouteQuery:
    origin: str
    destination_place_id: str
    mode: TravelMode
    direction: str
    departure_at: datetime
    goal_version: int


class RouteProvider(Protocol):
    def route(self, query: RouteQuery) -> RouteObservation: ...


class RouteCache:
    def __init__(self) -> None:
        self._values: dict[RouteQuery, RouteObservation] = {}

    def get(self, query: RouteQuery) -> RouteObservation | None:
        return self._values.get(query)

    def put(self, query: RouteQuery, route: RouteObservation) -> None:
        self._values[query] = route


class RouteEnricher:
    def __init__(
        self,
        provider: RouteProvider,
        quota: QuotaLedger,
        cache: RouteCache | None = None,
    ) -> None:
        self._provider = provider
        self._quota = quota
        self._cache = cache or RouteCache()

    def enrich(
        self,
        *,
        origin: str,
        goal: RoutingGoal,
        morning_at: datetime,
        return_at: datetime,
    ) -> tuple[tuple[RouteObservation, ...], tuple[RouteObservation, ...]]:
        results: list[list[RouteObservation]] = [[], []]
        for index, (direction, departure) in enumerate(
            (("morning", morning_at), ("return", return_at))
        ):
            for mode in sorted(goal.allowed_modes, key=lambda value: value.value):
                query = RouteQuery(
                    origin,
                    goal.destination.place_id,
                    mode,
                    direction,
                    departure,
                    goal.version,
                )
                cached = self._cache.get(query)
                if cached is None:
                    self._quota.reserve()
                    try:
                        cached = self._provider.route(query)
                    except Exception as error:
                        if (
                            "quota" in str(error).casefold()
                            or "resource_exhausted" in str(error).casefold()
                        ):
                            self._quota.trip_provider_quota(
                                type(self._provider).__name__
                            )
                        raise
                    self._cache.put(query, cached)
                results[index].append(cached)
        return tuple(results[0]), tuple(results[1])
