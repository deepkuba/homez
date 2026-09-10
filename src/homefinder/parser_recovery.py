"""Network-free replay planning and manually gated recovery batches."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    ArtifactRecoveryBindingRecord,
    DiagnosticRunRecord,
    ListingRecord,
    ListingSnapshotRecord,
    NetworkRecoveryCampaignRecord,
    NetworkRecoveryReleaseRecord,
    PageCaptureRecord,
    ParserReleaseRecord,
    PortalParserActivationRecord,
    ProductionParserResultRecord,
    ScrapeTaskRecord,
    SourceRecord,
    SourceRuntimeStateRecord,
    WorkflowJobRecord,
)
from homefinder.parsers.contracts import Portal

RecoveryBatch = Literal[50, 150, "remainder"]


@dataclass(frozen=True)
class NetworkReleasePreview:
    dry_run: bool
    allowed_batches: tuple[int, int, str]
    eligible: int
    released: int
    paused_reason: str | None = None


@dataclass(frozen=True)
class _RecoveryCandidate:
    listing: ListingRecord
    snapshot: ListingSnapshotRecord
    capture: PageCaptureRecord
    result: ProductionParserResultRecord
    diagnostic: DiagnosticRunRecord
    release: ParserReleaseRecord


class ParserRecoveryRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def plan_artifact_recovery(
        self,
        *,
        source: Portal,
        release_hash: str,
        activation_epoch: int,
        now: datetime,
    ) -> tuple[UUID, ...]:
        """Enqueue only retained artifacts for each listing's newest snapshot."""
        _inputs(source, release_hash, activation_epoch, now)
        with self._sessions.begin() as session:
            active = session.get(PortalParserActivationRecord, source)
            if active is None or (active.release_hash, active.activation_epoch) != (
                release_hash,
                activation_epoch,
            ):
                return ()
            snapshot_rows = session.execute(
                select(ListingRecord, ListingSnapshotRecord)
                .join(SourceRecord, SourceRecord.id == ListingRecord.source_id)
                .join(
                    ListingSnapshotRecord,
                    ListingSnapshotRecord.listing_id == ListingRecord.id,
                )
                .where(
                    SourceRecord.key == source,
                    ListingRecord.lifecycle_state != "inactive",
                )
            ).all()
            newest_snapshots: dict[UUID, ListingSnapshotRecord] = {}
            for listing, snapshot in snapshot_rows:
                prior_snapshot = newest_snapshots.get(listing.id)
                if prior_snapshot is None or (snapshot.observed_at, snapshot.id) > (
                    prior_snapshot.observed_at,
                    prior_snapshot.id,
                ):
                    newest_snapshots[listing.id] = snapshot
            capture_rows = session.execute(
                select(ListingSnapshotRecord.listing_id, PageCaptureRecord)
                .join(
                    PageCaptureRecord,
                    PageCaptureRecord.snapshot_id == ListingSnapshotRecord.id,
                )
                .where(
                    ListingSnapshotRecord.id.in_(
                        snapshot.id for snapshot in newest_snapshots.values()
                    )
                )
            ).all()
            newest_captures: dict[UUID, PageCaptureRecord] = {}
            for listing_id, capture in capture_rows:
                prior_capture = newest_captures.get(listing_id)
                if prior_capture is None or (capture.fetched_at, capture.id) > (
                    prior_capture.fetched_at,
                    prior_capture.id,
                ):
                    newest_captures[listing_id] = capture
            rows = session.execute(
                select(
                    ListingRecord,
                    ListingSnapshotRecord,
                    PageCaptureRecord,
                    ProductionParserResultRecord,
                    DiagnosticRunRecord,
                    ParserReleaseRecord,
                )
                .join(SourceRecord, SourceRecord.id == ListingRecord.source_id)
                .join(
                    ListingSnapshotRecord,
                    ListingSnapshotRecord.listing_id == ListingRecord.id,
                )
                .join(
                    PageCaptureRecord,
                    PageCaptureRecord.snapshot_id == ListingSnapshotRecord.id,
                )
                .join(
                    ProductionParserResultRecord,
                    ProductionParserResultRecord.capture_id == PageCaptureRecord.id,
                )
                .join(
                    DiagnosticRunRecord,
                    DiagnosticRunRecord.result_id == ProductionParserResultRecord.id,
                )
                .join(
                    ParserReleaseRecord,
                    ParserReleaseRecord.release_hash
                    == ProductionParserResultRecord.release_hash,
                )
                .where(
                    SourceRecord.key == source,
                    ListingRecord.lifecycle_state != "inactive",
                    DiagnosticRunRecord.artifact_status == "stored",
                    DiagnosticRunRecord.artifact_id.is_not(None),
                    DiagnosticRunRecord.expires_at > now,
                    ProductionParserResultRecord.release_hash != release_hash,
                )
            ).all()
            newest: dict[UUID, _RecoveryCandidate] = {}
            for row in rows:
                listing, snapshot, capture, *_ = row
                newest_snapshot = newest_snapshots.get(listing.id)
                newest_capture = newest_captures.get(listing.id)
                if (
                    newest_snapshot is None
                    or snapshot.id != newest_snapshot.id
                    or newest_capture is None
                    or capture.id != newest_capture.id
                ):
                    continue
                prior_candidate = newest.get(listing.id)
                candidate = _RecoveryCandidate(*row)
                if prior_candidate is None or (
                    snapshot.observed_at,
                    snapshot.id,
                    capture.fetched_at,
                    capture.id,
                ) > (
                    prior_candidate.snapshot.observed_at,
                    prior_candidate.snapshot.id,
                    prior_candidate.capture.fetched_at,
                    prior_candidate.capture.id,
                ):
                    newest[listing.id] = candidate
            planned = []
            for candidate in newest.values():
                listing = candidate.listing
                snapshot = candidate.snapshot
                capture = candidate.capture
                result = candidate.result
                diagnostic = candidate.diagnostic
                release = candidate.release
                if (
                    not json.loads(result.missing_fields_json)
                    and release.status != "revoked"
                ):
                    continue
                identity = hashlib.sha256(
                    f"{source}:{capture.content_hash}:{release_hash}".encode()
                ).hexdigest()
                existing = session.scalar(
                    select(ScrapeTaskRecord.id).where(
                        ScrapeTaskRecord.idempotency_key == identity
                    )
                )
                if existing is not None:
                    continue
                task_id = uuid4()
                session.add(
                    ScrapeTaskRecord(
                        id=task_id,
                        source=source,
                        snapshot_id=snapshot.id,
                        canonical_url=listing.canonical_url,
                        task_class="artifact_recovery",
                        release_hash=release_hash,
                        activation_epoch=activation_epoch,
                        idempotency_key=identity,
                        state="pending",
                        priority=10,
                        available_at=now,
                        created_at=now,
                        attempt_count=0,
                    )
                )
                session.flush()
                session.add(
                    ArtifactRecoveryBindingRecord(
                        task_id=task_id,
                        capture_id=capture.id,
                        artifact_id=diagnostic.artifact_id,
                        content_hash=capture.content_hash,
                        fetched_at=capture.fetched_at,
                        result_expires_at=result.expires_at,
                    )
                )
                planned.append(task_id)
            return tuple(planned)

    def release_network_batch(
        self,
        *,
        source: Portal,
        now: datetime,
        batch: RecoveryBatch | None = None,
        execute: bool = False,
        actor: str | None = None,
    ) -> NetworkReleasePreview:
        if source not in {"gratka", "morizon", "otodom", "olx"}:
            raise ValueError("invalid portal")
        if now.utcoffset() is None:
            raise ValueError("timezone-aware timestamp required")
        if batch not in {None, 50, 150, "remainder"}:
            raise ValueError("batch must be 50, 150, or remainder")
        with self._sessions.begin() as session:
            held = session.scalars(
                select(ScrapeTaskRecord)
                .where(
                    ScrapeTaskRecord.source == source,
                    ScrapeTaskRecord.task_class == "network_recovery",
                    ScrapeTaskRecord.state == "held",
                )
                .order_by(ScrapeTaskRecord.created_at, ScrapeTaskRecord.id)
                .with_for_update(skip_locked=True)
            ).all()
            runtime = session.get(SourceRuntimeStateRecord, source)
            paused = (
                "source-cooldown"
                if runtime is not None
                and runtime.cooldown_until is not None
                and runtime.cooldown_until > now
                else None
            )
            if not execute or batch is None or paused is not None:
                return NetworkReleasePreview(
                    True, (50, 150, "remainder"), len(held), 0, paused
                )
            if actor is None or not re.fullmatch(r"[^\s]{1,200}", actor):
                raise ValueError("executing recovery requires a bounded actor")
            active = session.get(PortalParserActivationRecord, source)
            if active is None:
                return NetworkReleasePreview(False, (50, 150, "remainder"), 0, 0)
            campaign = session.scalar(
                select(NetworkRecoveryCampaignRecord)
                .where(
                    NetworkRecoveryCampaignRecord.source == source,
                    NetworkRecoveryCampaignRecord.release_hash == active.release_hash,
                )
                .with_for_update()
            )
            if campaign is None:
                campaign = NetworkRecoveryCampaignRecord(
                    source=source,
                    release_hash=active.release_hash,
                    next_batch=0,
                    updated_at=now,
                )
                session.add(campaign)
                session.flush()
            sequence: tuple[RecoveryBatch, ...] = (50, 150, "remainder")
            if campaign.paused_reason is not None:
                return NetworkReleasePreview(
                    True,
                    (50, 150, "remainder"),
                    len(held),
                    0,
                    campaign.paused_reason,
                )
            if (
                campaign.next_batch >= len(sequence)
                or batch != sequence[campaign.next_batch]
            ):
                raise ValueError(
                    "recovery batches must run once in 50/150/remainder order"
                )
            count = len(held) if batch == "remainder" else min(batch, len(held))
            for task in held[:count]:
                task.state = "pending"
                task.available_at = now
            session.add(
                NetworkRecoveryReleaseRecord(
                    id=uuid4(),
                    source=source,
                    release_hash=active.release_hash,
                    activation_epoch=active.activation_epoch,
                    batch=str(batch),
                    actor=actor,
                    eligible_count=len(held),
                    released_count=count,
                    created_at=now,
                )
            )
            campaign.next_batch += 1
            campaign.updated_at = now
            return NetworkReleasePreview(
                False, (50, 150, "remainder"), len(held), count
            )

    def pause_network_recovery(
        self, *, source: Portal, release_hash: str, reason: str, now: datetime
    ) -> None:
        if reason not in {
            "portal-denial",
            "source-cooldown",
            "material-new-variant",
            "production-regression",
        }:
            raise ValueError("invalid recovery pause reason")
        with self._sessions.begin() as session:
            campaign = session.get(
                NetworkRecoveryCampaignRecord, (source, release_hash)
            )
            if campaign is None:
                campaign = NetworkRecoveryCampaignRecord(
                    source=source,
                    release_hash=release_hash,
                    next_batch=0,
                    updated_at=now,
                )
                session.add(campaign)
            campaign.paused_reason = reason
            campaign.updated_at = now

    def supersede_legacy_backlog(self, *, source: Portal, now: datetime) -> int:
        """Retain attempts and enqueue one held newest task per active listing."""
        with self._sessions.begin() as session:
            active = session.get(PortalParserActivationRecord, source)
            if active is None:
                raise ValueError("portal has no active parser")
            jobs = session.scalars(
                select(WorkflowJobRecord)
                .where(
                    WorkflowJobRecord.kind == "normalize",
                    WorkflowJobRecord.state.in_(("retry_wait", "dead_letter")),
                )
                .with_for_update(skip_locked=True)
            ).all()
            changed = 0
            listing_ids: set[UUID] = set()
            for job in jobs:
                try:
                    payload = json.loads(job.payload_json)
                except (TypeError, ValueError):
                    continue
                try:
                    snapshot_id = UUID(str(payload["snapshot_id"]))
                except (KeyError, ValueError):
                    continue
                snapshot = session.get(ListingSnapshotRecord, snapshot_id)
                listing = (
                    session.get(ListingRecord, snapshot.listing_id)
                    if snapshot
                    else None
                )
                origin = (
                    session.get(SourceRecord, listing.source_id) if listing else None
                )
                if listing is None or origin is None or origin.key != source:
                    continue
                listing_ids.add(listing.id)
                job.state = "superseded"
                job.finished_at = now
                job.updated_at = now
                job.last_error_code = "parser-recovery-superseded"
                job.lease_owner = None
                job.lease_token = None
                job.lease_expires_at = None
                changed += 1
            for listing_id in listing_ids:
                listing = session.get(ListingRecord, listing_id)
                if listing is None or listing.lifecycle_state == "inactive":
                    continue
                newest = session.scalar(
                    select(ListingSnapshotRecord)
                    .where(ListingSnapshotRecord.listing_id == listing_id)
                    .order_by(
                        ListingSnapshotRecord.observed_at.desc(),
                        ListingSnapshotRecord.id.desc(),
                    )
                    .limit(1)
                )
                if newest is None:
                    continue
                identity = hashlib.sha256(
                    f"network-recovery:{source}:{listing_id}:{active.release_hash}".encode()
                ).hexdigest()
                if session.scalar(
                    select(ScrapeTaskRecord.id).where(
                        ScrapeTaskRecord.idempotency_key == identity
                    )
                ):
                    continue
                session.add(
                    ScrapeTaskRecord(
                        id=uuid4(),
                        source=source,
                        snapshot_id=newest.id,
                        canonical_url=listing.canonical_url,
                        task_class="network_recovery",
                        release_hash=active.release_hash,
                        activation_epoch=active.activation_epoch,
                        idempotency_key=identity,
                        state="held",
                        priority=20,
                        available_at=now,
                        created_at=now,
                        attempt_count=0,
                    )
                )
            return changed


def _inputs(
    source: Portal, release_hash: str, activation_epoch: int, now: datetime
) -> None:
    if (
        source not in {"gratka", "morizon", "otodom", "olx"}
        or len(release_hash) != 64
        or activation_epoch < 1
        or now.utcoffset() is None
    ):
        raise ValueError("invalid recovery activation")


__all__ = ["NetworkReleasePreview", "ParserRecoveryRepository", "RecoveryBatch"]
