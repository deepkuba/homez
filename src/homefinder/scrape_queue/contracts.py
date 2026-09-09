"""Production work identity, deliberately excluding candidate benchmarks."""

from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID

from homefinder.parsers.contracts import Portal


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
