"""In-memory operational health snapshot, suitable for wiring to persistence later."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class HealthState(str, Enum):
    OK = "ok"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    state: HealthState
    checked_at: datetime
    detail: str = ""


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    status: str
    components: dict[str, ComponentHealth]
    oldest_pending_job: str | None
    oldest_pending_at: datetime | None


class HealthRegistry:
    def __init__(self) -> None:
        self._components: dict[str, ComponentHealth] = {}
        self._jobs: dict[str, datetime] = {}

    def update(
        self,
        name: str,
        state: HealthState,
        *,
        detail: str = "",
        checked_at: datetime | None = None,
    ) -> None:
        self._components[name] = ComponentHealth(
            state, checked_at or datetime.now(timezone.utc), detail
        )

    def set_job(self, name: str, oldest_pending_at: datetime | None) -> None:
        if oldest_pending_at is None:
            self._jobs.pop(name, None)
        else:
            self._jobs[name] = oldest_pending_at

    def snapshot(self, *, now: datetime | None = None) -> HealthSnapshot:
        del now  # Reserved for freshness policies owned by persistent adapters.
        status = (
            "ok"
            if all(item.state is HealthState.OK for item in self._components.values())
            else "degraded"
        )
        oldest = (
            min(self._jobs, key=lambda name: self._jobs[name]) if self._jobs else None
        )
        return HealthSnapshot(
            status,
            dict(self._components),
            oldest,
            self._jobs[oldest] if oldest is not None else None,
        )
