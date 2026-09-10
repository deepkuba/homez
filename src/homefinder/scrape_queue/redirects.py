"""Durable, validated cross-source redirect handoffs."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    RedirectHandoffRecord,
    ScrapeAttemptRecord,
    ScrapeTaskRecord,
)
from homefinder.parsers.contracts import Portal
from homefinder.scrape_queue.contracts import LostLease, ScrapeLease, WorkerIdentity
from homefinder.sources.portal_pages import validate_listing_url


class RedirectHandoffRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def handoff(
        self,
        worker: WorkerIdentity,
        lease: ScrapeLease,
        *,
        target_source: Portal,
        target_url: str,
        now: datetime,
    ) -> UUID:
        if now.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        if target_source == worker.source:
            raise ValueError("same-source redirect is not a handoff")
        canonical, listing_id = validate_listing_url(target_source, target_url)
        with self._sessions.begin() as session:
            existing = session.scalar(
                select(RedirectHandoffRecord).where(
                    RedirectHandoffRecord.source_task_id == lease.task_id,
                    RedirectHandoffRecord.target_source == target_source,
                    RedirectHandoffRecord.target_listing_id == listing_id,
                )
            )
            if existing is not None:
                return existing.id
            task = session.scalar(
                select(ScrapeTaskRecord)
                .where(
                    ScrapeTaskRecord.id == lease.task_id,
                    ScrapeTaskRecord.source == worker.source,
                    ScrapeTaskRecord.lease_owner == worker.worker_id,
                    ScrapeTaskRecord.lease_token == lease.lease_token,
                    ScrapeTaskRecord.state == "running",
                    ScrapeTaskRecord.lease_expires_at > now,
                )
                .with_for_update()
            )
            attempt = session.scalar(
                select(ScrapeAttemptRecord)
                .where(
                    ScrapeAttemptRecord.task_id == lease.task_id,
                    ScrapeAttemptRecord.lease_token == lease.lease_token,
                    ScrapeAttemptRecord.worker_id == worker.worker_id,
                )
                .with_for_update()
            )
            if task is None or attempt is None:
                raise LostLease("redirect handoff lease is invalid")
            handoff_id = uuid4()
            try:
                with session.begin_nested():
                    session.add(
                        RedirectHandoffRecord(
                            id=handoff_id,
                            source_task_id=task.id,
                            source=worker.source,
                            target_source=target_source,
                            target_listing_id=listing_id,
                            canonical_url=canonical,
                            state="pending",
                            created_at=now,
                        )
                    )
                    session.flush()
            except IntegrityError:
                winner = session.scalar(
                    select(RedirectHandoffRecord.id).where(
                        RedirectHandoffRecord.source_task_id == lease.task_id,
                        RedirectHandoffRecord.target_source == target_source,
                        RedirectHandoffRecord.target_listing_id == listing_id,
                    )
                )
                if winner is None:
                    raise
                return winner
            task.state = "succeeded"
            task.finished_at = now
            task.lease_owner = None
            task.lease_token = None
            task.lease_started_at = None
            task.lease_expires_at = None
            attempt.outcome = "succeeded"
            attempt.code = "redirected"
            attempt.finished_at = now
            return handoff_id
