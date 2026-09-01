"""Persistent job state machine with fenced leases and deterministic retry."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import WorkflowJobAttemptRecord, WorkflowJobRecord
from homefinder.workflow.models import ClaimedJob, JobState, LostLease


class WorkflowRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def enqueue(
        self,
        *,
        kind: str,
        idempotency_key: str,
        payload: dict[str, object],
        available_at: datetime,
        priority: int = 100,
        max_attempts: int = 8,
        parent_job_id: UUID | None = None,
        root_job_id: UUID | None = None,
    ) -> UUID:
        canonical = _canonical(payload)
        with self._sessions() as session:
            existing = session.scalar(
                select(WorkflowJobRecord).where(
                    WorkflowJobRecord.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                if existing.kind != kind or existing.payload_json != canonical:
                    raise ValueError("workflow idempotency key was reused")
                return existing.id
            now = datetime.now(timezone.utc)
            job_id = uuid4()
            session.add(
                WorkflowJobRecord(
                    id=job_id,
                    kind=kind,
                    idempotency_key=idempotency_key,
                    payload_json=canonical,
                    state=JobState.PENDING.value,
                    priority=priority,
                    available_at=available_at,
                    attempt_count=0,
                    max_attempts=max_attempts,
                    parent_job_id=parent_job_id,
                    root_job_id=root_job_id or job_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            try:
                session.commit()
                return job_id
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(WorkflowJobRecord).where(
                        WorkflowJobRecord.idempotency_key == idempotency_key
                    )
                )
                if (
                    existing is None
                    or existing.kind != kind
                    or existing.payload_json != canonical
                ):
                    raise
                return existing.id

    def claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_for: timedelta = timedelta(minutes=5),
    ) -> ClaimedJob | None:
        with self._sessions() as session:
            job = session.scalar(
                select(WorkflowJobRecord)
                .where(
                    WorkflowJobRecord.state.in_(
                        (JobState.PENDING.value, JobState.RETRY_WAIT.value)
                    ),
                    WorkflowJobRecord.available_at <= now,
                )
                .order_by(
                    WorkflowJobRecord.priority,
                    WorkflowJobRecord.available_at,
                    WorkflowJobRecord.created_at,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            token = uuid4()
            job.state = JobState.RUNNING.value
            job.attempt_count += 1
            job.lease_owner = worker_id
            job.lease_token = token
            job.lease_expires_at = now + lease_for
            job.started_at = job.started_at or now
            job.updated_at = now
            session.add(
                WorkflowJobAttemptRecord(
                    job_id=job.id,
                    attempt_number=job.attempt_count,
                    lease_token=token,
                    worker_id=worker_id,
                    started_at=now,
                )
            )
            session.commit()
            return ClaimedJob(
                id=job.id,
                kind=job.kind,
                payload=json.loads(job.payload_json),
                attempt_number=job.attempt_count,
                lease_token=token,
                lease_expires_at=now + lease_for,
            )

    def succeed(self, job: ClaimedJob, *, now: datetime) -> None:
        self._finish(job, now=now, state=JobState.SUCCEEDED, outcome="succeeded")

    def fail(
        self,
        job: ClaimedJob,
        *,
        now: datetime,
        code: str,
        detail: str,
        retryable: bool = True,
        manual_review: bool = False,
    ) -> JobState:
        bounded_code = code[:100]
        bounded_detail = detail[:500]
        with self._sessions() as session:
            record = self._leased(session, job)
            if manual_review:
                state = JobState.MANUAL_REVIEW
            elif retryable and record.attempt_count < record.max_attempts:
                state = JobState.RETRY_WAIT
                record.available_at = now + retry_delay(record.id, record.attempt_count)
            else:
                state = JobState.DEAD_LETTER
            record.state = state.value
            record.updated_at = now
            record.last_error_code = bounded_code
            record.last_error_detail = bounded_detail
            record.lease_owner = None
            record.lease_token = None
            record.lease_expires_at = None
            if state in {JobState.DEAD_LETTER, JobState.MANUAL_REVIEW}:
                record.finished_at = now
            attempt = session.get(
                WorkflowJobAttemptRecord, (record.id, record.attempt_count)
            )
            if attempt is None:
                raise LostLease("workflow attempt is missing")
            attempt.finished_at = now
            attempt.outcome = state.value
            attempt.error_code = bounded_code
            attempt.error_detail = bounded_detail
            session.commit()
            return state

    def reap_expired(self, *, now: datetime) -> int:
        with self._sessions() as session:
            jobs = session.scalars(
                select(WorkflowJobRecord).where(
                    WorkflowJobRecord.state == JobState.RUNNING.value,
                    WorkflowJobRecord.lease_expires_at < now,
                )
            ).all()
            for job in jobs:
                job.state = JobState.RETRY_WAIT.value
                job.available_at = now
                job.lease_owner = None
                job.lease_token = None
                job.lease_expires_at = None
                job.last_error_code = "lease-expired"
                job.last_error_detail = "worker lease expired before acknowledgement"
                job.updated_at = now
                attempt = session.get(
                    WorkflowJobAttemptRecord, (job.id, job.attempt_count)
                )
                if attempt is not None and attempt.finished_at is None:
                    attempt.finished_at = now
                    attempt.outcome = "lease-expired"
            session.commit()
            return len(jobs)

    def status(self) -> dict[str, object]:
        with self._sessions() as session:
            counts = session.execute(
                select(
                    WorkflowJobRecord.state, func.count(WorkflowJobRecord.id)
                ).group_by(WorkflowJobRecord.state)
            ).all()
            oldest = session.scalar(
                select(func.min(WorkflowJobRecord.available_at)).where(
                    WorkflowJobRecord.state.in_(
                        (
                            JobState.PENDING.value,
                            JobState.RETRY_WAIT.value,
                            JobState.RUNNING.value,
                        )
                    )
                )
            )
            return {
                "counts": {str(state): int(count) for state, count in counts},
                "oldest_pending_at": (
                    None if oldest is None else _aware(oldest).isoformat()
                ),
            }

    def _finish(
        self,
        job: ClaimedJob,
        *,
        now: datetime,
        state: JobState,
        outcome: str,
    ) -> None:
        with self._sessions() as session:
            record = self._leased(session, job)
            record.state = state.value
            record.updated_at = now
            record.finished_at = now
            record.lease_owner = None
            record.lease_token = None
            record.lease_expires_at = None
            attempt = session.get(
                WorkflowJobAttemptRecord, (record.id, record.attempt_count)
            )
            if attempt is None:
                raise LostLease("workflow attempt is missing")
            attempt.finished_at = now
            attempt.outcome = outcome
            session.commit()

    @staticmethod
    def _leased(session: Session, job: ClaimedJob) -> WorkflowJobRecord:
        record = session.scalar(
            select(WorkflowJobRecord)
            .where(
                WorkflowJobRecord.id == job.id,
                WorkflowJobRecord.state == JobState.RUNNING.value,
                WorkflowJobRecord.lease_token == job.lease_token,
            )
            .with_for_update()
        )
        if record is None:
            raise LostLease("workflow lease is no longer valid")
        return record


def retry_delay(job_id: UUID, attempt: int) -> timedelta:
    base = min(21_600, 30 * (2 ** max(0, attempt - 1)))
    digest = hashlib.sha256(f"{job_id}:{attempt}".encode()).digest()
    jitter = int.from_bytes(digest[:2], "big") % max(1, base // 5 + 1)
    return timedelta(seconds=base + jitter)


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
