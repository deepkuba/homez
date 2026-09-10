"""Network-free capacity evidence under explicit, successful-response assumptions.

This model does not configure production pacing or override denial/cooldown rules.
It models two source-pinned worker slots and one aggregate source start interval.
The daily scenario and the simultaneous 45-task burst are independent scenarios.
"""

from dataclasses import dataclass
from math import ceil, isfinite
from typing import Literal

from homefinder.parsers.contracts import Portal


@dataclass(frozen=True)
class CapacityEvidence:
    source: Portal
    daily_successes: int
    daily_last_completion_seconds: float
    burst_starts_seconds: tuple[float, ...]
    burst_completions_seconds: tuple[float, ...]
    burst_p95_seconds: float
    slo_status: Literal["available", "unavailable"]


def simulate_capacity(
    source: Portal,
    *,
    interval_seconds: float = 10,
    response_seconds: float = 10,
    daily_attempt_limit: int = 1000,
    daily_success_limit: int = 1000,
) -> CapacityEvidence:
    """Assess a policy without sleeping, excluding or retrying any arrived task.

    All modeled responses succeed. Denial-deferred traffic is measured by the
    coordinator separately; this optimistic capacity estimate is not a live SLO
    measurement or evidence of compliance with a portal's reviewed source policy.
    """
    if (
        source not in {"gratka", "morizon", "otodom", "olx"}
        or not isfinite(interval_seconds)
        or interval_seconds <= 0
        or not isfinite(response_seconds)
        or response_seconds < 0
        or daily_attempt_limit < 1
        or daily_success_limit < 1
    ):
        raise ValueError("invalid capacity profile")

    ceiling = min(daily_attempt_limit, daily_success_limit)
    _, daily_completions = _schedule(
        tuple(index * 86400 / 1000 for index in range(1000)),
        interval_seconds,
        response_seconds,
    )
    daily_within_day = tuple(time for time in daily_completions if time < 86400)
    daily_successes = min(len(daily_within_day), ceiling)
    starts, completions = _schedule((0.0,) * 45, interval_seconds, response_seconds)
    p95 = sorted(completions)[ceil(len(completions) * 0.95) - 1]
    return CapacityEvidence(
        source=source,
        daily_successes=daily_successes,
        daily_last_completion_seconds=(
            daily_within_day[daily_successes - 1] if daily_successes else 0
        ),
        burst_starts_seconds=starts,
        burst_completions_seconds=completions,
        burst_p95_seconds=p95,
        slo_status=(
            "available" if daily_successes == 1000 and p95 <= 600 else "unavailable"
        ),
    )


def _schedule(
    arrivals: tuple[float, ...], interval: float, response: float
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    workers_free_at = [0.0, 0.0]
    next_source_start = 0.0
    starts: list[float] = []
    completions: list[float] = []
    for arrival in arrivals:
        worker = min(range(2), key=workers_free_at.__getitem__)
        start = max(arrival, next_source_start, workers_free_at[worker])
        completion = start + response
        workers_free_at[worker] = completion
        next_source_start = start + interval
        starts.append(start)
        completions.append(completion)
    return tuple(starts), tuple(completions)
