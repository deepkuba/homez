"""Coordinator-owned PostgreSQL queue with row locks and fenced acknowledgements."""

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    ListingRecord,
    ListingSnapshotRecord,
    PageCaptureRecord,
    PortalParserActivationRecord,
    ProductionFieldCandidateRecord,
    ProductionParserResultRecord,
    ScrapeAttemptRecord,
    ScraperWorkerRecord,
    ScrapeTaskRecord,
    SourceRecord,
)
from homefinder.parsers.contracts import FieldCandidate, PageFacts, ParserResult, Portal
from homefinder.scrape_queue.contracts import (
    CaptureOutcome,
    LostLease,
    QueuePolicy,
    ScrapeLease,
    StatusPage,
    TaskClass,
    TaskStatus,
    WorkerIdentity,
)
from homefinder.sources.portal_pages import validate_listing_url

FAILURE_CODES = frozenset({"transport-error", "parser-error", "invalid-target"})
DEFER_CODES = frozenset({"portal-denied", "source-cooldown", "budget-exhausted"})
SUCCESS_CODES = frozenset({"downloaded", "partial", "artifact-unavailable"})
PRIORITIES = {
    TaskClass.LIVE: 0,
    TaskClass.ARTIFACT_RECOVERY: 10,
    TaskClass.NETWORK_RECOVERY: 20,
}


class ScrapeQueueRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        policy: QueuePolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self.policy = policy or QueuePolicy()
        self._clock = clock

    def with_clock(self, clock: Callable[[], datetime]) -> "ScrapeQueueRepository":
        """Bind the coordinator clock, including checks made after waiting on locks."""
        return ScrapeQueueRepository(self._sessions, policy=self.policy, clock=clock)

    def _current(self, supplied: datetime) -> datetime:
        current = supplied if self._clock is None else self._clock()
        require_aware(current)
        return current

    def enqueue(
        self,
        *,
        source: Portal,
        snapshot_id: UUID,
        now: datetime,
        task_class: TaskClass = TaskClass.LIVE,
    ) -> UUID:
        require_aware(now)
        task_class = TaskClass(task_class)
        with self._sessions.begin() as session:
            active = self._active(session, source)
            snapshot = session.get(ListingSnapshotRecord, snapshot_id)
            listing = (
                session.get(ListingRecord, snapshot.listing_id) if snapshot else None
            )
            origin = session.get(SourceRecord, listing.source_id) if listing else None
            if listing is None or origin is None or origin.key != source:
                raise ValueError("snapshot source mismatch")
            canonical, identity = validate_listing_url(source, listing.canonical_url)
            if identity != listing.source_listing_id:
                raise ValueError("listing identity mismatch")
            identity_key = f"{source}:{snapshot_id}:{canonical}:{task_class.value}"
            if task_class != TaskClass.LIVE:
                identity_key += f":{active.release_hash}:{active.activation_epoch}"
            key = hashlib.sha256(identity_key.encode()).hexdigest()
            existing = session.scalar(
                select(ScrapeTaskRecord.id).where(
                    ScrapeTaskRecord.idempotency_key == key
                )
            )
            if existing is not None:
                return existing
            task_id = uuid4()
            try:
                with session.begin_nested():
                    session.add(
                        ScrapeTaskRecord(
                            id=task_id,
                            source=source,
                            snapshot_id=snapshot_id,
                            canonical_url=canonical,
                            task_class=task_class.value,
                            release_hash=active.release_hash,
                            activation_epoch=active.activation_epoch,
                            idempotency_key=key,
                            priority=PRIORITIES[task_class],
                            state="held"
                            if task_class == TaskClass.NETWORK_RECOVERY
                            else "pending",
                            available_at=now,
                            created_at=now,
                            attempt_count=0,
                        )
                    )
                    session.flush()
            except IntegrityError:
                winner = session.scalar(
                    select(ScrapeTaskRecord.id).where(
                        ScrapeTaskRecord.idempotency_key == key
                    )
                )
                if winner is None:
                    raise
                return winner
            return task_id

    def register_worker(
        self,
        worker: WorkerIdentity,
        *,
        release_hashes: tuple[str, ...],
        now: datetime,
        healthy: bool = True,
    ) -> None:
        require_aware(now)
        if len(release_hashes) > 64 or any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None for value in release_hashes
        ):
            raise ValueError("invalid release capabilities")
        encoded = json.dumps(sorted(set(release_hashes)))
        with self._sessions.begin() as session:
            row = session.scalar(
                select(ScraperWorkerRecord)
                .where(ScraperWorkerRecord.worker_id == worker.worker_id)
                .with_for_update()
            )
            if row is None:
                try:
                    with session.begin_nested():
                        row = ScraperWorkerRecord(
                            worker_id=worker.worker_id,
                            source=worker.source,
                            deployment=worker.deployment,
                            heartbeat_at=now,
                            release_hashes_json=encoded,
                            healthy=healthy,
                        )
                        session.add(row)
                        session.flush()
                except IntegrityError:
                    row = session.scalar(
                        select(ScraperWorkerRecord)
                        .where(ScraperWorkerRecord.worker_id == worker.worker_id)
                        .with_for_update()
                    )
            if row is None or (row.source, row.deployment) != (
                worker.source,
                worker.deployment,
            ):
                raise ValueError("worker identity is immutable")
            if aware(row.heartbeat_at) > now:
                raise ValueError("worker heartbeat cannot go backwards")
            row.heartbeat_at = now
            row.release_hashes_json = encoded
            row.healthy = healthy

    def claim(
        self,
        worker: WorkerIdentity,
        *,
        now: datetime,
        task_classes: tuple[TaskClass, ...] = tuple(TaskClass),
    ) -> ScrapeLease | None:
        require_aware(now)
        classes = tuple(TaskClass(item).value for item in task_classes)
        self.reap_expired(now=now)
        with self._sessions.begin() as session:
            active = session.scalar(
                select(PortalParserActivationRecord)
                .where(PortalParserActivationRecord.source == worker.source)
                .with_for_update(read=True)
            )
            if active is None:
                return None
            row = session.scalar(
                select(ScraperWorkerRecord)
                .where(ScraperWorkerRecord.worker_id == worker.worker_id)
                .with_for_update(read=True)
            )
            if (
                row is None
                or not row.healthy
                or (row.source, row.deployment) != (worker.source, worker.deployment)
                or aware(row.heartbeat_at)
                <= now - timedelta(seconds=self.policy.worker_health_seconds)
                or active.release_hash not in json.loads(row.release_hashes_json)
            ):
                return None
            task = session.scalar(
                select(ScrapeTaskRecord)
                .where(
                    ScrapeTaskRecord.source == worker.source,
                    ScrapeTaskRecord.task_class.in_(classes),
                    ScrapeTaskRecord.state.in_(("pending", "deferred")),
                    ScrapeTaskRecord.available_at <= now,
                    ScrapeTaskRecord.attempt_count < self.policy.max_attempts,
                )
                .order_by(
                    ScrapeTaskRecord.priority,
                    ScrapeTaskRecord.available_at,
                    ScrapeTaskRecord.created_at,
                    ScrapeTaskRecord.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                return None
            now = self._current(now)
            if aware(row.heartbeat_at) <= now - timedelta(
                seconds=self.policy.worker_health_seconds
            ):
                return None
            task.release_hash = active.release_hash
            task.activation_epoch = active.activation_epoch
            task.state = "running"
            task.attempt_count += 1
            task.lease_owner = worker.worker_id
            task.lease_token = uuid4()
            task.lease_started_at = now
            task.lease_expires_at = now + timedelta(seconds=self.policy.lease_seconds)
            session.add(
                ScrapeAttemptRecord(
                    task_id=task.id,
                    attempt_number=task.attempt_count,
                    lease_token=task.lease_token,
                    worker_id=worker.worker_id,
                    release_hash=task.release_hash,
                    activation_epoch=task.activation_epoch,
                    started_at=now,
                    route_class="unassigned",
                    response_bytes=0,
                )
            )
            return ScrapeLease(
                task_id=task.id,
                source=cast(Portal, task.source),
                snapshot_id=task.snapshot_id,
                canonical_url=task.canonical_url,
                task_class=TaskClass(task.task_class),
                release_hash=task.release_hash,
                activation_epoch=task.activation_epoch,
                lease_token=task.lease_token,
                lease_expires_at=task.lease_expires_at,
                attempt_number=task.attempt_count,
            )

    def complete(
        self,
        worker: WorkerIdentity,
        lease: ScrapeLease,
        outcome: CaptureOutcome,
        *,
        now: datetime,
    ) -> None:
        require_aware(now)
        require_aware(outcome.fetched_at)
        result = outcome.result
        if (
            lease.task_class not in {TaskClass.LIVE, TaskClass.NETWORK_RECOVERY}
            or not 0 < outcome.size_bytes <= 2_000_000
            or re.fullmatch(r"[0-9a-f]{64}", outcome.content_hash) is None
            or outcome.capture_id != result.capture_id
            or result.release_hash != lease.release_hash
            or outcome.fetched_at > now
            or len(result.candidates) > 64
            or len(result.missing_fields) > 32
            or re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", result.variant) is None
            or any(
                re.fullmatch(r"[a-z_]{1,50}", name) is None
                for name in result.missing_fields
            )
        ):
            raise ValueError("invalid production capture")
        for candidate in result.candidates:
            if (
                candidate.release_hash != lease.release_hash
                or len(candidate.name) > 80
                or len(candidate.origin) > 80
                or len(candidate.locator) > 200
                or len(json.dumps(candidate.value)) > 25_000
            ):
                raise ValueError("invalid production candidate")
        encoded = TypeAdapter(CaptureOutcome).dump_json(outcome)
        if len(encoded) > 120_000:
            raise ValueError("production result too large")
        result_hash = hashlib.sha256(encoded).hexdigest()
        with self._sessions.begin() as session:
            # Match claim/activation lock ordering: portal pointer, then task.
            self._active(session, worker.source)
            task = session.scalar(
                select(ScrapeTaskRecord)
                .where(ScrapeTaskRecord.id == lease.task_id)
                .with_for_update()
            )
            prior = session.scalar(
                select(ProductionParserResultRecord).where(
                    ProductionParserResultRecord.task_id == lease.task_id
                )
            )
            if prior is not None:
                attempt = session.get(
                    ScrapeAttemptRecord, (lease.task_id, lease.attempt_number)
                )
                if (
                    task is not None
                    and task.source == worker.source
                    and task.task_class == lease.task_class.value
                    and task.snapshot_id == lease.snapshot_id
                    and prior.release_hash == lease.release_hash
                    and prior.activation_epoch == lease.activation_epoch
                    and prior.result_hash == result_hash
                    and attempt is not None
                    and attempt.lease_token == lease.lease_token
                    and attempt.worker_id == worker.worker_id
                ):
                    return
                raise LostLease("lease completion differs from accepted outcome")
            task = self._leased(session, worker, lease, now)
            if task.lease_started_at is None or outcome.fetched_at < aware(
                task.lease_started_at
            ):
                raise ValueError("capture predates network lease")
            try:
                expires_at = outcome.fetched_at.replace(
                    year=outcome.fetched_at.year + 2
                )
            except ValueError:
                expires_at = outcome.fetched_at.replace(
                    year=outcome.fetched_at.year + 2, day=28
                )
            session.add(
                PageCaptureRecord(
                    id=outcome.capture_id,
                    snapshot_id=task.snapshot_id,
                    fetched_at=outcome.fetched_at,
                    content_hash=outcome.content_hash,
                    size_bytes=outcome.size_bytes,
                )
            )
            session.flush()
            result_id = uuid4()
            session.add(
                ProductionParserResultRecord(
                    id=result_id,
                    task_id=task.id,
                    capture_id=outcome.capture_id,
                    release_hash=task.release_hash,
                    activation_epoch=task.activation_epoch,
                    variant=result.variant,
                    facts_json=result.facts.model_dump_json(),
                    missing_fields_json=json.dumps(result.missing_fields),
                    result_hash=result_hash,
                    expires_at=expires_at,
                )
            )
            session.flush()
            for position, candidate in enumerate(result.candidates):
                session.add(
                    ProductionFieldCandidateRecord(
                        result_id=result_id,
                        position=position,
                        name=candidate.name,
                        value_json=json.dumps(candidate.value),
                        origin=candidate.origin,
                        locator=candidate.locator,
                    )
                )
            attempt = session.get(ScrapeAttemptRecord, (task.id, task.attempt_count))
            if attempt is None:
                raise LostLease("lease attempt missing")
            attempt.finished_at = self._current(now)
            attempt.outcome = "succeeded"
            attempt.code = (
                "artifact-unavailable" if result.missing_fields else "downloaded"
            )
            attempt.response_bytes = outcome.size_bytes
            task.state = "succeeded"
            task.finished_at = self._current(now)
            self._clear_lease(task)

    def outcome(
        self,
        *,
        source: Portal,
        snapshot_id: UUID,
        now: datetime | None = None,
    ) -> ParserResult | None:
        now = now or datetime.now(timezone.utc)
        require_aware(now)
        with self._sessions() as session:
            row = session.scalar(
                select(ProductionParserResultRecord)
                .join(
                    ScrapeTaskRecord,
                    ScrapeTaskRecord.id == ProductionParserResultRecord.task_id,
                )
                .where(
                    ScrapeTaskRecord.source == source,
                    ScrapeTaskRecord.snapshot_id == snapshot_id,
                    ScrapeTaskRecord.state == "succeeded",
                    ProductionParserResultRecord.expires_at > now,
                )
                .order_by(ScrapeTaskRecord.finished_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            candidates = session.scalars(
                select(ProductionFieldCandidateRecord)
                .where(ProductionFieldCandidateRecord.result_id == row.id)
                .order_by(ProductionFieldCandidateRecord.position)
            ).all()
            return ParserResult(
                row.capture_id,
                row.release_hash,
                row.variant,
                tuple(
                    FieldCandidate(
                        item.name,
                        json.loads(item.value_json),
                        item.origin,
                        item.locator,
                        row.release_hash,
                    )
                    for item in candidates
                ),
                tuple(json.loads(row.missing_fields_json)),
                facts=PageFacts.model_validate_json(row.facts_json),
            )

    def task_state(self, task_id: UUID) -> str | None:
        with self._sessions() as session:
            return session.scalar(
                select(ScrapeTaskRecord.state).where(ScrapeTaskRecord.id == task_id)
            )

    def heartbeat(
        self, worker: WorkerIdentity, lease: ScrapeLease, *, now: datetime
    ) -> ScrapeLease:
        require_aware(now)
        with self._sessions.begin() as session:
            task = self._leased(session, worker, lease, now)
            now = self._current(now)
            if task.lease_started_at is None:
                raise LostLease("lease start is missing")
            expires = min(
                now + timedelta(seconds=self.policy.lease_seconds),
                aware(task.lease_started_at)
                + timedelta(seconds=self.policy.max_lease_seconds),
            )
            if expires <= now:
                raise LostLease("lease renewal limit reached")
            task.lease_expires_at = expires
            return replace(lease, lease_expires_at=expires)

    def succeed(
        self,
        worker: WorkerIdentity,
        lease: ScrapeLease,
        *,
        now: datetime,
        code: str = "downloaded",
        response_bytes: int = 0,
    ) -> None:
        if code not in SUCCESS_CODES:
            raise ValueError("invalid success code")
        self._finish(
            worker,
            lease,
            now=now,
            code=code,
            state="succeeded",
            response_bytes=response_bytes,
        )

    def fail(
        self, worker: WorkerIdentity, lease: ScrapeLease, *, now: datetime, code: str
    ) -> str:
        if code not in FAILURE_CODES:
            raise ValueError("invalid failure code")
        return self._finish(
            worker,
            lease,
            now=now,
            code=code,
            state="failed",
            retry=code == "transport-error",
        )

    def defer(
        self,
        worker: WorkerIdentity,
        lease: ScrapeLease,
        *,
        now: datetime,
        available_at: datetime,
        code: str,
    ) -> None:
        require_aware(available_at)
        if code not in DEFER_CODES or available_at <= now:
            raise ValueError("invalid deferral")
        self._finish(
            worker,
            lease,
            now=now,
            code=code,
            state="deferred",
            available_at=available_at,
        )

    def _finish(
        self,
        worker: WorkerIdentity,
        lease: ScrapeLease,
        *,
        now: datetime,
        code: str,
        state: str,
        response_bytes: int = 0,
        retry: bool = False,
        available_at: datetime | None = None,
    ) -> str:
        require_aware(now)
        if not 0 <= response_bytes <= 2_000_000:
            raise ValueError("invalid response byte count")
        with self._sessions.begin() as session:
            task = self._leased(session, worker, lease, now)
            now = self._current(now)
            attempt = session.get(ScrapeAttemptRecord, (task.id, task.attempt_count))
            if attempt is None or attempt.finished_at is not None:
                raise LostLease("lease attempt is missing or completed")
            if retry and task.attempt_count < self.policy.max_attempts:
                state = "deferred"
                available_at = now + timedelta(
                    seconds=min(21600, 30 * 2 ** (task.attempt_count - 1))
                )
            if state == "deferred" and task.attempt_count >= self.policy.max_attempts:
                state = "failed"
            task.state = state
            if state == "deferred" and available_at is not None:
                task.available_at = available_at
            if state in {"succeeded", "failed"}:
                task.finished_at = now
            attempt.finished_at = now
            attempt.outcome = state
            attempt.code = code
            attempt.response_bytes = response_bytes
            self._clear_lease(task)
            return state

    def reap_expired(self, *, now: datetime, limit: int = 100) -> int:
        require_aware(now)
        if not 1 <= limit <= 500:
            raise ValueError("invalid reap limit")
        with self._sessions.begin() as session:
            tasks = session.scalars(
                select(ScrapeTaskRecord)
                .where(
                    ScrapeTaskRecord.state == "running",
                    ScrapeTaskRecord.lease_expires_at <= now,
                )
                .order_by(ScrapeTaskRecord.lease_expires_at, ScrapeTaskRecord.id)
                .with_for_update(skip_locked=True)
                .limit(limit)
            ).all()
            for task in tasks:
                task.state = (
                    "failed"
                    if task.attempt_count >= self.policy.max_attempts
                    else "pending"
                )
                task.available_at = now
                if task.state == "failed":
                    task.finished_at = now
                attempt = session.get(
                    ScrapeAttemptRecord, (task.id, task.attempt_count)
                )
                if attempt is not None:
                    attempt.finished_at = now
                    attempt.outcome = "lease-expired"
                    attempt.code = "lease-expired"
                self._clear_lease(task)
            return len(tasks)

    def status(
        self,
        *,
        source: Portal,
        limit: int = 50,
        before: str | None = None,
    ) -> StatusPage:
        if not 1 <= limit <= 500:
            raise ValueError("invalid status limit")
        query = select(ScrapeTaskRecord).where(ScrapeTaskRecord.source == source)
        if before is not None:
            try:
                if len(before) > 300:
                    raise ValueError
                cursor = json.loads(base64.urlsafe_b64decode(before))
                if (
                    not isinstance(cursor, list)
                    or len(cursor) != 3
                    or not all(isinstance(value, str) for value in cursor)
                ):
                    raise ValueError
                cursor_source, date, identifier = cursor
                if cursor_source != source:
                    raise ValueError
                created_at, task_id = datetime.fromisoformat(date), UUID(identifier)
                require_aware(created_at)
            except (ValueError, TypeError, binascii.Error) as error:
                raise ValueError("invalid status cursor") from error
            query = query.where(
                or_(
                    ScrapeTaskRecord.created_at < created_at,
                    and_(
                        ScrapeTaskRecord.created_at == created_at,
                        ScrapeTaskRecord.id < task_id,
                    ),
                )
            )
        with self._sessions() as session:
            rows = session.scalars(
                query.order_by(
                    ScrapeTaskRecord.created_at.desc(), ScrapeTaskRecord.id.desc()
                ).limit(limit + 1)
            ).all()
            items = tuple(
                TaskStatus(
                    task_id=row.id,
                    source=row.source,
                    task_class=row.task_class,
                    state=row.state,
                    available_at=aware(row.available_at),
                    attempt_count=row.attempt_count,
                    created_at=aware(row.created_at),
                )
                for row in rows[:limit]
            )
            cursor_out = None
            if len(rows) > limit:
                last = items[-1]
                cursor_out = base64.urlsafe_b64encode(
                    json.dumps(
                        [source, last.created_at.isoformat(), str(last.task_id)]
                    ).encode()
                ).decode()
            return StatusPage(items, cursor_out)

    def _leased(
        self,
        session: Session,
        worker: WorkerIdentity,
        lease: ScrapeLease,
        now: datetime,
    ) -> ScrapeTaskRecord:
        active = session.scalar(
            select(PortalParserActivationRecord)
            .where(PortalParserActivationRecord.source == worker.source)
            .with_for_update(read=True)
        )
        identity = session.get(ScraperWorkerRecord, worker.worker_id)
        if identity is None or (identity.source, identity.deployment) != (
            worker.source,
            worker.deployment,
        ):
            raise LostLease("lease worker identity mismatch")
        task = session.scalar(
            select(ScrapeTaskRecord)
            .where(
                ScrapeTaskRecord.id == lease.task_id,
                ScrapeTaskRecord.source == worker.source,
                ScrapeTaskRecord.state == "running",
                ScrapeTaskRecord.lease_owner == worker.worker_id,
                ScrapeTaskRecord.lease_token == lease.lease_token,
                ScrapeTaskRecord.lease_expires_at > now,
            )
            .with_for_update()
        )
        now = self._current(now)
        if (
            task is None
            or task.lease_expires_at is None
            or aware(task.lease_expires_at) <= now
            or active is None
            or task.task_class != lease.task_class.value
            or task.snapshot_id != lease.snapshot_id
            or task.canonical_url != lease.canonical_url
            or task.attempt_count != lease.attempt_number
            or (task.release_hash, task.activation_epoch)
            != (active.release_hash, active.activation_epoch)
            or (lease.source, lease.release_hash, lease.activation_epoch)
            != (task.source, task.release_hash, task.activation_epoch)
        ):
            raise LostLease("lease is expired, replaced, or from a withdrawn epoch")
        return task

    @staticmethod
    def _active(session: Session, source: Portal) -> PortalParserActivationRecord:
        active = session.scalar(
            select(PortalParserActivationRecord)
            .where(PortalParserActivationRecord.source == source)
            .with_for_update(read=True)
        )
        if active is None:
            raise ValueError("no active parser release")
        return active

    @staticmethod
    def _clear_lease(task: ScrapeTaskRecord) -> None:
        task.lease_owner = None
        task.lease_token = None
        task.lease_expires_at = None
        task.lease_started_at = None


def require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
