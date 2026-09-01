"""Versioned commute goals and conservative multimodal route evaluation."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class TravelMode(str, Enum):
    DRIVE = "drive"
    TRANSIT = "transit"
    WALK = "walk"
    BICYCLE = "bicycle"


class CommuteStatus(str, Enum):
    PASS = "pass"  # noqa: S105 - a status label, not a credential
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Destination:
    place_id: str
    address: str


@dataclass(frozen=True, slots=True)
class RoutingGoal:
    version: int
    destination: Destination
    max_minutes: int = 45
    allowed_modes: frozenset[TravelMode] = frozenset(TravelMode)
    stale_after: timedelta = timedelta(days=7)
    required: bool = True


@dataclass(frozen=True, slots=True)
class RouteObservation:
    mode: TravelMode
    duration_minutes: int
    provider: str
    observed_at: datetime
    confidence: float = 1.0
    advisories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommuteEvaluation:
    status: CommuteStatus
    morning_minutes: int | None
    return_minutes: int | None
    controlling_minutes: int | None
    margin_minutes: int | None
    winning_modes: tuple[TravelMode | None, TravelMode | None]
    confidence: float
    stale: bool
    explanation: str


def evaluate_commute(
    goal: RoutingGoal,
    *,
    morning: tuple[RouteObservation, ...],
    return_routes: tuple[RouteObservation, ...],
    evaluated_at: datetime,
) -> CommuteEvaluation:
    morning_best = _best(goal, morning, evaluated_at)
    return_best = _best(goal, return_routes, evaluated_at)
    stale = morning_best[2] or return_best[2]
    if morning_best[0] is None or return_best[0] is None or stale:
        return CommuteEvaluation(
            CommuteStatus.UNKNOWN,
            morning_best[0],
            return_best[0],
            None,
            None,
            (morning_best[1], return_best[1]),
            min(morning_best[3], return_best[3]),
            stale,
            "route data is stale or missing",
        )
    controlling = max(morning_best[0], return_best[0])
    margin = goal.max_minutes - controlling
    status = CommuteStatus.PASS if margin >= 0 else CommuteStatus.FAIL
    return CommuteEvaluation(
        status,
        morning_best[0],
        return_best[0],
        controlling,
        margin,
        (morning_best[1], return_best[1]),
        min(morning_best[3], return_best[3]),
        False,
        f"{status.value} by {abs(margin)} minutes",
    )


def _best(
    goal: RoutingGoal,
    routes: tuple[RouteObservation, ...],
    evaluated_at: datetime,
) -> tuple[int | None, TravelMode | None, bool, float]:
    allowed = [route for route in routes if route.mode in goal.allowed_modes]
    usable = [
        route
        for route in allowed
        if evaluated_at - route.observed_at <= goal.stale_after
    ]
    if not usable:
        return None, None, bool(allowed), 0.0
    best = min(usable, key=lambda route: route.duration_minutes)
    return best.duration_minutes, best.mode, False, best.confidence
