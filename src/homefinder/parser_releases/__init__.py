"""Immutable parser releases and portal-scoped manual activation."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    ParserActivationAuditRecord,
    ParserReleaseRecord,
    PortalParserActivationRecord,
    ScraperWorkerRecord,
    SourceRecord,
)
from homefinder.parsers.contracts import Portal

_HASH = re.compile(r"[0-9a-f]{64}")
_GIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_SAFE_ID = re.compile(r"[a-zA-Z0-9_.:-]{1,100}")


class ActivationRejected(RuntimeError):
    """A manual pointer change did not satisfy its fail-closed gates."""


@dataclass(frozen=True)
class ReleaseBuild:
    source: Portal
    parser_version: str
    git_commit: str
    parser_content_hash: str
    configuration_hash: str
    dependency_lock_hash: str
    deployable_digest: str
    qualifying_benchmark_run: str

    def __post_init__(self) -> None:
        if (
            self.source not in {"gratka", "morizon", "otodom", "olx"}
            or _SAFE_ID.fullmatch(self.parser_version) is None
            or _GIT.fullmatch(self.git_commit) is None
            or any(
                _HASH.fullmatch(value) is None
                for value in (
                    self.parser_content_hash,
                    self.configuration_hash,
                    self.dependency_lock_hash,
                )
            )
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.deployable_digest) is None
            or _SAFE_ID.fullmatch(self.qualifying_benchmark_run) is None
        ):
            raise ValueError("invalid parser release provenance")

    @property
    def release_hash(self) -> str:
        payload = json.dumps(
            {
                "source": self.source,
                "parser_content_hash": self.parser_content_hash,
                "configuration_hash": self.configuration_hash,
                "dependency_lock_hash": self.dependency_lock_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class RegisteredRelease:
    source: Portal
    release_hash: str
    status: str


class ParserReleaseRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        worker_health_seconds: int = 90,
    ) -> None:
        self._sessions = sessions
        self._worker_health = timedelta(seconds=worker_health_seconds)

    def register(self, build: ReleaseBuild, *, now: datetime) -> RegisteredRelease:
        _aware(now)
        release_hash = build.release_hash
        values = asdict(build)
        with self._sessions.begin() as session:
            existing = session.get(ParserReleaseRecord, release_hash)
            if existing is not None:
                persisted = {name: getattr(existing, name) for name in values}
                if persisted != values:
                    raise ValueError("release hash provenance mismatch")
                return RegisteredRelease(build.source, release_hash, existing.status)
            session.add(
                ParserReleaseRecord(
                    release_hash=release_hash,
                    created_at=now,
                    status="draft",
                    **values,
                )
            )
        return RegisteredRelease(build.source, release_hash, "draft")

    def activate(
        self,
        *,
        source: Portal,
        release_hash: str,
        actor: str,
        compared_metrics: str,
        now: datetime,
        expected_epoch: int | None = None,
    ) -> int:
        return self._change(
            source=source,
            release_hash=release_hash,
            actor=actor,
            compared_metrics=compared_metrics,
            now=now,
            action="activate",
            expected_epoch=expected_epoch,
        )

    def rollback(
        self,
        *,
        source: Portal,
        actor: str,
        reason: str,
        now: datetime,
        expected_epoch: int,
    ) -> int:
        _audit_text(actor, reason)
        with self._sessions() as session:
            pointer = session.get(PortalParserActivationRecord, source)
            if pointer is None:
                raise ActivationRejected("portal has no active release")
            prior = session.scalar(
                select(ParserActivationAuditRecord)
                .where(
                    ParserActivationAuditRecord.source == source,
                    ParserActivationAuditRecord.release_hash == pointer.release_hash,
                    ParserActivationAuditRecord.previous_release_hash.is_not(None),
                )
                .order_by(ParserActivationAuditRecord.activation_epoch.desc())
                .limit(1)
            )
            target = prior.previous_release_hash if prior is not None else None
        if target is None:
            raise ActivationRejected("no rollback release recorded")
        return self._change(
            source=source,
            release_hash=target,
            actor=actor,
            compared_metrics=reason,
            now=now,
            action="rollback",
            expected_epoch=expected_epoch,
        )

    def _change(
        self,
        *,
        source: Portal,
        release_hash: str,
        actor: str,
        compared_metrics: str,
        now: datetime,
        action: str,
        expected_epoch: int | None,
    ) -> int:
        _aware(now)
        _audit_text(actor, compared_metrics)
        with self._sessions.begin() as session:
            # Serialize the first activation before a pointer exists.
            source_row = session.scalar(
                select(SourceRecord).where(SourceRecord.key == source).with_for_update()
            )
            if source_row is None:
                raise ActivationRejected("portal is not configured")
            pointer = session.scalar(
                select(PortalParserActivationRecord)
                .where(PortalParserActivationRecord.source == source)
                .with_for_update()
            )
            current_hash = pointer.release_hash if pointer is not None else None
            current_epoch = pointer.activation_epoch if pointer is not None else 0
            if expected_epoch is not None and expected_epoch != current_epoch:
                raise ActivationRejected("activation epoch changed")
            candidate = session.get(ParserReleaseRecord, release_hash)
            if candidate is None or candidate.source != source:
                raise ActivationRejected("release does not belong to portal")
            if candidate.status == "revoked":
                raise ActivationRejected("revoked release cannot be activated")
            if action == "activate" and candidate.qualifying_benchmark_run is None:
                raise ActivationRejected("release lacks qualifying benchmark evidence")
            required = {release_hash}
            if current_hash is not None:
                required.add(current_hash)
            workers = session.scalars(
                select(ScraperWorkerRecord)
                .where(
                    ScraperWorkerRecord.source == source,
                    ScraperWorkerRecord.healthy.is_(True),
                    ScraperWorkerRecord.heartbeat_at > now - self._worker_health,
                )
                .with_for_update(read=True)
            ).all()
            capable = set()
            for worker in workers:
                advertised = set(json.loads(worker.release_hashes_json))
                if required <= advertised:
                    capable.add(worker.deployment)
            if capable != {"nas", "vps"}:
                raise ActivationRejected(
                    "healthy NAS and VPS workers must advertise candidate and rollback"
                )
            next_epoch = current_epoch + 1
            if pointer is None:
                pointer = PortalParserActivationRecord(
                    source=source,
                    release_hash=release_hash,
                    activation_epoch=next_epoch,
                )
                session.add(pointer)
            else:
                pointer.release_hash = release_hash
                pointer.activation_epoch = next_epoch
            if current_hash is not None and current_hash != release_hash:
                withdrawn = session.get(ParserReleaseRecord, current_hash)
                if withdrawn is not None:
                    withdrawn.status = "revoked" if action == "rollback" else "retired"
            candidate.status = "active"
            session.add(
                ParserActivationAuditRecord(
                    id=uuid4(),
                    source=source,
                    activation_epoch=next_epoch,
                    previous_release_hash=current_hash,
                    release_hash=release_hash,
                    action=action,
                    actor=actor,
                    compared_metrics=compared_metrics,
                    occurred_at=now,
                )
            )
            return next_epoch


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")


def _audit_text(actor: str, detail: str) -> None:
    if not 1 <= len(actor) <= 200 or not 1 <= len(detail) <= 1000:
        raise ValueError("bounded activation audit fields required")


__all__ = [
    "ActivationRejected",
    "ParserReleaseRepository",
    "RegisteredRelease",
    "ReleaseBuild",
]
