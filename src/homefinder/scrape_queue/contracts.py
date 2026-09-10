"""Production work identity, deliberately excluding candidate benchmarks."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID

from homefinder.parsers.contracts import ParserResult, Portal


class TaskClass(str, Enum):
    LIVE = "live"
    ARTIFACT_RECOVERY = "artifact_recovery"
    NETWORK_RECOVERY = "network_recovery"


@dataclass(frozen=True)
class TaskIdentity:
    task_id: UUID
    source: Portal
    snapshot_id: UUID
    canonical_url: str = field(repr=False)
    task_class: TaskClass
    release_hash: str
    activation_epoch: int


class LostLease(RuntimeError):
    """An expired, replaced, foreign, or withdrawn-epoch lease."""


@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: str
    source: Portal
    deployment: str

    def __post_init__(self) -> None:
        import re

        if (
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", self.worker_id)
            or self.source not in {"gratka", "morizon", "otodom", "olx"}
            or self.deployment not in {"nas", "vps"}
        ):
            raise ValueError("invalid worker identity")


@dataclass(frozen=True)
class QueuePolicy:
    lease_seconds: int = 60
    heartbeat_seconds: int = 20
    max_lease_seconds: int = 600
    worker_health_seconds: int = 90
    max_attempts: int = 8
    metadata_retention_days: int = 30

    def __post_init__(self) -> None:
        if not (
            1 <= self.heartbeat_seconds < self.lease_seconds <= 300
            and self.lease_seconds <= self.max_lease_seconds <= 3600
            and self.heartbeat_seconds * 2 <= self.worker_health_seconds <= 600
            and 1 <= self.max_attempts <= 32
            and 1 <= self.metadata_retention_days <= 365
        ):
            raise ValueError("invalid queue timing or retention policy")


@dataclass(frozen=True)
class ScrapeLease:
    task_id: UUID
    source: Portal
    snapshot_id: UUID
    canonical_url: str = field(repr=False)
    task_class: TaskClass
    release_hash: str
    activation_epoch: int
    lease_token: UUID = field(repr=False)
    lease_expires_at: datetime
    attempt_number: int


@dataclass(frozen=True)
class TaskStatus:
    task_id: UUID
    source: str
    task_class: str
    state: str
    available_at: datetime
    attempt_count: int
    created_at: datetime


@dataclass(frozen=True)
class StatusPage:
    items: tuple[TaskStatus, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class CaptureOutcome:
    """Metadata and production parse only; raw bytes cannot cross this boundary."""

    capture_id: UUID
    fetched_at: datetime
    content_hash: str
    size_bytes: int
    result: ParserResult = field(repr=False)
    artifact_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class SourceBudgetPolicy:
    minimum_interval: timedelta
    daily_attempt_limit: int
    daily_success_limit: int
    policy_version: str = "reference-v1"

    def __post_init__(self) -> None:
        if (
            self.minimum_interval <= timedelta(0)
            or self.minimum_interval > timedelta(hours=1)
            or not 1 <= self.daily_success_limit <= self.daily_attempt_limit <= 100_000
            or not self.policy_version
            or len(self.policy_version) > 80
        ):
            raise ValueError("invalid source budget policy")


@dataclass(frozen=True)
class NetworkPermit:
    granted: bool
    available_at: datetime
    route_class: str | None = None
    route_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class SourceBudgetSnapshot:
    source: str
    attempt_count: int
    success_count: int
    cooldown_until: datetime | None


@dataclass(frozen=True)
class ProxyBudgetSnapshot:
    allocated_bytes: int
    transferred_bytes: int
    usable_limit_bytes: int = 900_000_000
    reserved_allowance_bytes: int = 100_000_000
