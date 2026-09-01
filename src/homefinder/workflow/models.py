from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: UUID
    kind: str
    payload: dict[str, object]
    attempt_number: int
    lease_token: UUID
    lease_expires_at: datetime


class LostLease(RuntimeError):
    pass


class ManualReviewRequired(ValueError):
    pass


class PermanentWorkflowError(RuntimeError):
    pass
