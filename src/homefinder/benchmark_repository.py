"""Persistence adapter for non-production benchmark records."""

import json
import os
from base64 import b64encode
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import TypeAdapter
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from homefinder.benchmark import (
    BenchmarkEntryResult,
    BenchmarkManifest,
    DifferenceReview,
    eligible_for_activation,
)
from homefinder.catalog.orm import (
    BenchmarkDifferenceReviewRecord,
    BenchmarkFieldCandidateRecord,
    BenchmarkManifestRecord,
    BenchmarkResultRecord,
    BenchmarkRunRecord,
)
from homefinder.parsers.contracts import ParserResult, Portal

_RESULT = TypeAdapter(ParserResult)


class BenchmarkRepository:
    def __init__(self, sessions: sessionmaker[Session], *, detail_key: bytes) -> None:
        if len(detail_key) != 32:
            raise ValueError("benchmark detail key must contain 32 bytes")
        self._sessions = sessions
        self._cipher = AESGCM(detail_key)

    def _encrypt(self, run_id: str, entry_id: str, side: str, value: bytes) -> str:
        nonce = os.urandom(12)
        aad = f"homez-benchmark-v1:{run_id}:{entry_id}:{side}".encode()
        return b64encode(nonce + self._cipher.encrypt(nonce, value, aad)).decode()

    def create(self, run_id: str, manifest: BenchmarkManifest) -> None:
        if not 1 <= len(run_id) <= 100:
            raise ValueError("invalid benchmark identifier")
        entries = json.dumps(
            [asdict(entry) for entry in manifest.entries], default=str, sort_keys=True
        )
        strata = json.dumps(
            {
                "eligible": manifest.eligible_by_stratum,
                "selected": manifest.selected_by_stratum,
            },
            sort_keys=True,
        )
        with self._sessions.begin() as session:
            prior = session.get(BenchmarkManifestRecord, manifest.manifest_id)
            if prior is None:
                session.add(
                    BenchmarkManifestRecord(
                        id=manifest.manifest_id,
                        source=manifest.source,
                        active_release_hash=manifest.active_release_hash,
                        candidate_release_hash=manifest.candidate_release_hash,
                        created_at=manifest.created_at,
                        selection_policy=manifest.selection_policy,
                        entries_json=entries,
                        strata_json=strata,
                    )
                )
            elif (prior.entries_json, prior.strata_json) != (entries, strata):
                raise ValueError("immutable benchmark manifest mismatch")
            session.add(
                BenchmarkRunRecord(
                    id=run_id,
                    manifest_id=manifest.manifest_id,
                    source=manifest.source,
                    candidate_release_hash=manifest.candidate_release_hash,
                    state="pending",
                    processed_count=0,
                    total_count=len(manifest.entries),
                    unavailable_count=0,
                    eligible=False,
                    created_at=manifest.created_at,
                )
            )

    def complete(
        self,
        run_id: str,
        results: Iterable[BenchmarkEntryResult],
        reviews: Iterable[DifferenceReview],
        *,
        changed_variants: frozenset[str],
        now: datetime,
    ) -> bool:
        result_rows = tuple(results)
        review_rows = tuple(reviews)
        eligible = eligible_for_activation(
            results=result_rows,
            reviews=review_rows,
            changed_variants=changed_variants,
            evaluated_at=now,
        )
        with self._sessions.begin() as session:
            run = session.scalar(
                select(BenchmarkRunRecord)
                .where(BenchmarkRunRecord.id == run_id)
                .with_for_update()
            )
            if run is None or run.state != "pending":
                raise ValueError("benchmark run is not pending")
            for item in result_rows:
                session.add(
                    BenchmarkResultRecord(
                        run_id=run_id,
                        entry_id=item.entry_id,
                        input_kind=item.kind,
                        artifact_id=(
                            item.entry_id if item.kind == "artifact" else None
                        ),
                        variant=item.variant,
                        status=item.status,
                        active_result_json=(
                            self._encrypt(
                                run_id,
                                item.entry_id,
                                "active",
                                _RESULT.dump_json(item.active),
                            )
                            if item.active is not None
                            else None
                        ),
                        candidate_result_json=(
                            self._encrypt(
                                run_id,
                                item.entry_id,
                                "candidate",
                                _RESULT.dump_json(item.candidate),
                            )
                            if item.candidate is not None
                            else None
                        ),
                        expires_at=item.expires_at,
                    )
                )
                for side, parsed in (
                    ("active", item.active),
                    ("candidate", item.candidate),
                ):
                    if parsed is None:
                        continue
                    for position, field in enumerate(parsed.candidates):
                        session.add(
                            BenchmarkFieldCandidateRecord(
                                run_id=run_id,
                                entry_id=item.entry_id,
                                parser_side=side,
                                position=position,
                                name=field.name,
                                value_json=self._encrypt(
                                    run_id,
                                    item.entry_id,
                                    f"{side}:{position}",
                                    json.dumps(field.value).encode(),
                                ),
                                origin=field.origin,
                                locator=field.locator,
                                expires_at=item.expires_at,
                            )
                        )
            for review in review_rows:
                session.add(
                    BenchmarkDifferenceReviewRecord(
                        run_id=run_id,
                        signature=review.signature,
                        state=review.state,
                        candidate_adds_value=review.candidate_adds_value,
                        reason=review.reason,
                        reviewed_at=now if review.state != "unreviewed" else None,
                    )
                )
            run.processed_count = len(result_rows)
            run.unavailable_count = sum(
                item.status == "benchmark-input-unavailable" for item in result_rows
            )
            run.state = "complete"
            run.eligible = eligible
            run.completed_at = now
        return eligible

    def expire_artifact(self, artifact_id: str) -> int:
        """Erase detailed raw-derived output while retaining aggregate tombstones."""
        with self._sessions.begin() as session:
            rows = session.scalars(
                select(BenchmarkResultRecord).where(
                    BenchmarkResultRecord.artifact_id == artifact_id
                )
            ).all()
            keys = [(row.run_id, row.entry_id) for row in rows]
            for run_id, entry_id in keys:
                session.execute(
                    delete(BenchmarkFieldCandidateRecord).where(
                        BenchmarkFieldCandidateRecord.run_id == run_id,
                        BenchmarkFieldCandidateRecord.entry_id == entry_id,
                    )
                )
                session.execute(
                    update(BenchmarkResultRecord)
                    .where(
                        BenchmarkResultRecord.run_id == run_id,
                        BenchmarkResultRecord.entry_id == entry_id,
                    )
                    .values(
                        active_result_json=None,
                        candidate_result_json=None,
                        artifact_id=None,
                    )
                )
            return len(keys)


def persisted_eligibility(
    session: Session,
    source: Portal,
    release_hash: str,
    run_id: str,
    now: datetime,
) -> bool:
    run = session.get(BenchmarkRunRecord, run_id)
    expired_or_missing = session.scalar(
        select(BenchmarkResultRecord.entry_id)
        .where(
            BenchmarkResultRecord.run_id == run_id,
            BenchmarkResultRecord.input_kind == "artifact",
            (
                (BenchmarkResultRecord.expires_at.is_(None))
                | (BenchmarkResultRecord.expires_at <= now)
                | (BenchmarkResultRecord.status != "compared")
            ),
        )
        .limit(1)
    )
    return bool(
        run is not None
        and run.source == source
        and run.candidate_release_hash == release_hash
        and run.state == "complete"
        and run.eligible
        and expired_or_missing is None
    )


__all__ = ["BenchmarkRepository", "persisted_eligibility"]
