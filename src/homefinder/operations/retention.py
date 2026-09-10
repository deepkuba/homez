"""Bounded production retention with persistent, content-free alert state."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    ArtifactRecoveryBindingRecord,
    BenchmarkFieldCandidateRecord,
    BenchmarkResultRecord,
    BenchmarkRetentionTombstoneRecord,
    BenchmarkRunRecord,
    DatabaseSizeAlertStateRecord,
    DiagnosticRunRecord,
    ListingRecord,
    ListingRetentionTombstoneRecord,
    ListingSnapshotRecord,
    PageCaptureRecord,
    ProductionFieldCandidateRecord,
    ProductionParserResultRecord,
    ProductionResolvedFieldRecord,
    RetentionAlertStateRecord,
    RetentionJobRunRecord,
    SourceRecord,
)

TEN_GB = 10_000_000_000


@dataclass(frozen=True)
class OperationalAlert:
    kind: str
    safe_detail: str
    occurred_at: datetime


@dataclass(frozen=True)
class RetentionRun:
    cutoff: datetime
    deleted_results: int
    deleted_benchmark_results: int
    failed_results: int
    backlog_count: int
    oldest_remaining_fetched_at: datetime | None
    database_size_before: int | None
    database_size_after: int | None
    healthy: bool


@dataclass(frozen=True)
class RetentionStatus:
    healthy: bool | None
    last_finished_at: datetime | None
    backlog_count: int
    database_size_bytes: int | None
    armed_boundaries: tuple[int, ...]


class ProductionRetentionRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        notify: Callable[[OperationalAlert], None] | None = None,
        measure_database: Callable[[Session], tuple[int, tuple[tuple[str, int], ...]]]
        | None = None,
    ) -> None:
        self._sessions = sessions
        self._notify = notify or (lambda _message: None)
        self._measure_database = measure_database or _measure_database

    def run_daily(
        self,
        *,
        now: datetime,
        batch_size: int = 500,
        started_at: datetime | None = None,
    ) -> RetentionRun:
        _aware(now)
        if batch_size < 1 or batch_size > 10_000:
            raise ValueError("batch_size must be between 1 and 10000")
        cutoff = _two_year_cutoff(now)
        started = started_at or now
        size_before: int | None = None
        size_after: int | None = None
        largest: tuple[tuple[str, int], ...] = ()
        deleted = 0
        deleted_benchmark = 0
        failed = 0
        error_code: str | None = None
        try:
            with self._sessions.begin() as session:
                size_before, largest = self._measure_database(session)
                result_ids = session.scalars(
                    select(ProductionParserResultRecord.id)
                    .join(
                        PageCaptureRecord,
                        PageCaptureRecord.id == ProductionParserResultRecord.capture_id,
                    )
                    .where(PageCaptureRecord.fetched_at < cutoff)
                    .order_by(
                        PageCaptureRecord.fetched_at, ProductionParserResultRecord.id
                    )
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                ).all()
                for result_id in result_ids:
                    self._purge_production_result(session, result_id, now)
                    deleted += 1
                remaining = batch_size - deleted
                if remaining:
                    benchmark_keys = session.execute(
                        select(
                            BenchmarkResultRecord.run_id, BenchmarkResultRecord.entry_id
                        )
                        .where(
                            BenchmarkResultRecord.expires_at.is_not(None),
                            BenchmarkResultRecord.expires_at <= now,
                        )
                        .order_by(
                            BenchmarkResultRecord.expires_at,
                            BenchmarkResultRecord.run_id,
                            BenchmarkResultRecord.entry_id,
                        )
                        .limit(remaining)
                        .with_for_update(skip_locked=True)
                    ).all()
                    for run_id, entry_id in benchmark_keys:
                        self._purge_benchmark_result(session, run_id, entry_id, now)
                        deleted_benchmark += 1
                backlog = (
                    session.scalar(
                        select(func.count())
                        .select_from(ProductionParserResultRecord)
                        .join(
                            PageCaptureRecord,
                            PageCaptureRecord.id
                            == ProductionParserResultRecord.capture_id,
                        )
                        .where(PageCaptureRecord.fetched_at < cutoff)
                    )
                    or 0
                )
                oldest = session.scalar(
                    select(func.min(PageCaptureRecord.fetched_at))
                    .select_from(ProductionParserResultRecord)
                    .join(
                        PageCaptureRecord,
                        PageCaptureRecord.id == ProductionParserResultRecord.capture_id,
                    )
                )
                size_after, largest = self._measure_database(session)
                healthy = backlog == 0
                error_code = None if healthy else "expired-backlog"
                session.add(
                    RetentionJobRunRecord(
                        id=uuid4(),
                        started_at=started,
                        finished_at=now,
                        cutoff=cutoff,
                        deleted_results=deleted,
                        deleted_benchmark_results=deleted_benchmark,
                        failed_results=failed,
                        backlog_count=backlog,
                        oldest_remaining_fetched_at=oldest,
                        database_size_before=size_before,
                        database_size_after=size_after,
                        duration_ms=max(0, int((now - started).total_seconds() * 1000)),
                        healthy=healthy,
                        error_code=error_code,
                    )
                )
        except Exception:
            with suppress(Exception):
                self.record_database_size(now=now, size_bytes=None)
            with suppress(Exception), self._sessions.begin() as session:
                session.add(
                    RetentionJobRunRecord(
                        id=uuid4(),
                        started_at=started,
                        finished_at=now,
                        cutoff=cutoff,
                        deleted_results=0,
                        deleted_benchmark_results=0,
                        failed_results=1,
                        backlog_count=0,
                        oldest_remaining_fetched_at=None,
                        database_size_before=size_before,
                        database_size_after=None,
                        duration_ms=max(0, int((now - started).total_seconds() * 1000)),
                        healthy=False,
                        error_code="retention-run-failed",
                    )
                )
            with suppress(Exception):
                self.record_retention_health(
                    now=now, healthy=False, error_code="retention-run-failed"
                )
            raise
        self.record_retention_health(now=now, healthy=healthy, error_code=error_code)
        self.record_database_size(
            now=now,
            size_bytes=size_after,
            largest_relations=largest,
            size_before=size_before,
            oldest_remaining_fetched_at=oldest,
        )
        return RetentionRun(
            cutoff,
            deleted,
            deleted_benchmark,
            failed,
            backlog,
            oldest,
            size_before,
            size_after,
            healthy,
        )

    def _purge_production_result(
        self, session: Session, result_id: UUID, now: datetime
    ) -> None:
        result = session.get(ProductionParserResultRecord, result_id)
        if result is None:
            return
        capture = session.get(PageCaptureRecord, result.capture_id)
        if capture is None:
            return
        snapshot = session.get(ListingSnapshotRecord, capture.snapshot_id)
        listing = session.get(ListingRecord, snapshot.listing_id) if snapshot else None
        source = session.get(SourceRecord, listing.source_id) if listing else None
        other_listing_result = (
            session.scalar(
                select(ProductionParserResultRecord.id)
                .join(
                    PageCaptureRecord,
                    PageCaptureRecord.id == ProductionParserResultRecord.capture_id,
                )
                .join(
                    ListingSnapshotRecord,
                    ListingSnapshotRecord.id == PageCaptureRecord.snapshot_id,
                )
                .where(
                    ListingSnapshotRecord.listing_id == listing.id,
                    ProductionParserResultRecord.id != result_id,
                )
                .limit(1)
            )
            if listing is not None
            else None
        )
        if listing is not None and source is not None and other_listing_result is None:
            tombstone = session.get(ListingRetentionTombstoneRecord, listing.id)
            values = dict(
                source=source.key,
                source_listing_id=listing.source_listing_id,
                canonical_url_hash=hashlib.sha256(
                    listing.canonical_url.encode()
                ).hexdigest(),
                confirmed_inactive=listing.lifecycle_state == "inactive",
                last_fetched_at=capture.fetched_at,
                detailed_data_deleted_at=now,
                schema_version=1,
            )
            if tombstone is None:
                session.add(
                    ListingRetentionTombstoneRecord(listing_id=listing.id, **values)
                )
            elif capture.fetched_at >= tombstone.last_fetched_at:
                for name, value in values.items():
                    setattr(tombstone, name, value)
        session.execute(
            delete(DiagnosticRunRecord).where(
                DiagnosticRunRecord.result_id == result_id
            )
        )
        session.execute(
            delete(ProductionFieldCandidateRecord).where(
                ProductionFieldCandidateRecord.result_id == result_id
            )
        )
        session.execute(
            delete(ProductionResolvedFieldRecord).where(
                ProductionResolvedFieldRecord.result_id == result_id
            )
        )
        session.delete(result)
        session.flush()
        if (
            session.scalar(
                select(ProductionParserResultRecord.id)
                .where(ProductionParserResultRecord.capture_id == capture.id)
                .limit(1)
            )
            is None
        ):
            session.execute(
                delete(ArtifactRecoveryBindingRecord).where(
                    ArtifactRecoveryBindingRecord.capture_id == capture.id
                )
            )
            session.delete(capture)

    def _purge_benchmark_result(
        self, session: Session, run_id: str, entry_id: str, now: datetime
    ) -> None:
        result = session.get(BenchmarkResultRecord, (run_id, entry_id))
        if result is None:
            return
        run = session.get(BenchmarkRunRecord, run_id)
        if run is None:
            return
        session.merge(
            BenchmarkRetentionTombstoneRecord(
                run_id=run_id,
                entry_id=entry_id,
                artifact_id=result.artifact_id,
                source=run.source,
                variant=result.variant,
                status=result.status,
                deleted_at=now,
            )
        )
        session.execute(
            delete(BenchmarkFieldCandidateRecord).where(
                BenchmarkFieldCandidateRecord.run_id == run_id,
                BenchmarkFieldCandidateRecord.entry_id == entry_id,
            )
        )
        result.active_result_json = None
        result.candidate_result_json = None

    def record_database_size(
        self,
        *,
        now: datetime,
        size_bytes: int | None,
        largest_relations: tuple[tuple[str, int], ...] = (),
        size_before: int | None = None,
        oldest_remaining_fetched_at: datetime | None = None,
    ) -> None:
        _aware(now)
        alerts: list[OperationalAlert] = []
        with self._sessions.begin() as session:
            states = session.scalars(
                select(DatabaseSizeAlertStateRecord).with_for_update()
            ).all()
            if size_bytes is None:
                for state in states:
                    state.below_streak = 0
                    state.last_measured_at = now
                return
            if size_bytes < 0:
                raise ValueError("database size must be non-negative")
            highest = size_bytes // TEN_GB
            for multiplier in range(1, highest + 1):
                boundary = multiplier * TEN_GB
                boundary_state = session.get(DatabaseSizeAlertStateRecord, boundary)
                if boundary_state is None:
                    boundary_state = DatabaseSizeAlertStateRecord(
                        boundary_bytes=boundary,
                        armed=True,
                        below_streak=0,
                    )
                    session.add(boundary_state)
                if boundary_state.armed:
                    boundary_state.armed = False
                    boundary_state.last_alerted_at = now
                    alerts.append(
                        OperationalAlert(
                            "database-size",
                            _size_detail(
                                boundary,
                                size_bytes,
                                size_before,
                                largest_relations,
                                oldest_remaining_fetched_at,
                            ),
                            now,
                        )
                    )
                boundary_state.below_streak = 0
                boundary_state.last_measured_at = now
            for existing_state in states:
                if existing_state.boundary_bytes > size_bytes:
                    existing_state.below_streak += 1
                    if existing_state.below_streak >= 7:
                        existing_state.armed = True
                    existing_state.last_measured_at = now
        for alert in alerts:
            self._notify(alert)

    def record_retention_health(
        self, *, now: datetime, healthy: bool, error_code: str | None = None
    ) -> None:
        _aware(now)
        alert: OperationalAlert | None = None
        with self._sessions.begin() as session:
            state = session.get(RetentionAlertStateRecord, "production")
            if state is None:
                state = RetentionAlertStateRecord(key="production", failing=False)
                session.add(state)
            if not healthy:
                code = error_code or "unknown"
                if not state.failing:
                    state.failing = True
                    state.first_failed_at = now
                    state.last_notified_at = now
                    alert = OperationalAlert(
                        "retention-failure",
                        f"error={code}; dashboard=/feedback/scraper-errors",
                        now,
                    )
                elif state.last_notified_at is None or now - _db_aware(
                    state.last_notified_at
                ) >= timedelta(days=1):
                    state.last_notified_at = now
                    alert = OperationalAlert(
                        "retention-reminder",
                        f"error={code}; dashboard=/feedback/scraper-errors",
                        now,
                    )
                state.last_error_code = code
            elif state.failing:
                state.failing = False
                state.last_error_code = None
                state.last_notified_at = now
                alert = OperationalAlert(
                    "retention-recovery",
                    "status=healthy; dashboard=/feedback/scraper-errors",
                    now,
                )
        if alert is not None:
            self._notify(alert)

    def status(self) -> RetentionStatus:
        """Return bounded aggregate state suitable for an authenticated dashboard."""
        with self._sessions() as session:
            latest = session.scalar(
                select(RetentionJobRunRecord)
                .order_by(RetentionJobRunRecord.finished_at.desc())
                .limit(1)
            )
            armed = session.scalars(
                select(DatabaseSizeAlertStateRecord.boundary_bytes)
                .where(DatabaseSizeAlertStateRecord.armed.is_(True))
                .order_by(DatabaseSizeAlertStateRecord.boundary_bytes)
            ).all()
            return RetentionStatus(
                healthy=latest.healthy if latest is not None else None,
                last_finished_at=latest.finished_at if latest is not None else None,
                backlog_count=latest.backlog_count if latest is not None else 0,
                database_size_bytes=(
                    latest.database_size_after if latest is not None else None
                ),
                armed_boundaries=tuple(armed),
            )


def _two_year_cutoff(now: datetime) -> datetime:
    try:
        return now.replace(year=now.year - 2)
    except ValueError:
        return now.replace(year=now.year - 2, day=28)


def _measure_database(session: Session) -> tuple[int, tuple[tuple[str, int], ...]]:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        size = int(
            session.scalar(select(func.pg_database_size(func.current_database()))) or 0
        )
        rows = session.execute(
            text(
                "SELECT schemaname || '.' || relname, pg_total_relation_size(relid) "
                "FROM pg_catalog.pg_statio_user_tables "
                "ORDER BY pg_total_relation_size(relid) DESC, schemaname, relname "
                "LIMIT 10"
            )
        ).all()
        return size, tuple((str(name), int(value)) for name, value in rows)
    page_count = int(session.execute(text("PRAGMA page_count")).scalar_one())
    page_size = int(session.execute(text("PRAGMA page_size")).scalar_one())
    return page_count * page_size, ()


def _size_detail(
    boundary: int,
    size: int,
    before: int | None,
    relations: tuple[tuple[str, int], ...],
    oldest: datetime | None,
) -> str:
    safe_relations = ",".join(f"{name}:{value}" for name, value in relations[:10])
    return (
        f"boundary={boundary}; size={size}; before={before}; "
        f"relations={safe_relations}; "
        f"oldest={oldest.isoformat() if oldest else 'none'}; "
        "dashboard=/feedback/scraper-errors"
    )


def _aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")


def _db_aware(value: datetime) -> datetime:
    return (
        value if value.utcoffset() is not None else value.replace(tzinfo=timezone.utc)
    )


__all__ = [
    "OperationalAlert",
    "ProductionRetentionRepository",
    "RetentionRun",
    "RetentionStatus",
]
