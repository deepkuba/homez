"""Deterministic offline candidate benchmark execution."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from homefinder.parsers.contracts import PageInput, ParserResult, Portal

MAX_RAW_ENTRIES = 1_000
SELECTION_POLICY = "stratified-v1"


@dataclass(frozen=True)
class RawCandidate:
    artifact_id: str
    capture_id: UUID
    content_hash: str
    fetched_at: datetime
    variant: str
    stratum: str
    structural_signature: str
    expires_at: datetime


@dataclass(frozen=True)
class ManifestEntry:
    entry_id: str
    kind: Literal["artifact", "fixture"]
    content_hash: str
    variant: str
    stratum: str
    expires_at: datetime | None = None


@dataclass(frozen=True)
class BenchmarkManifest:
    manifest_id: str
    source: Portal
    active_release_hash: str
    candidate_release_hash: str
    entries: tuple[ManifestEntry, ...]
    created_at: datetime
    selection_policy: str
    eligible_by_stratum: tuple[tuple[str, int], ...]
    selected_by_stratum: tuple[tuple[str, int], ...]

    @property
    def artifact_ids(self) -> frozenset[str]:
        return frozenset(
            entry.entry_id for entry in self.entries if entry.kind == "artifact"
        )


def build_manifest(
    *,
    source: Portal,
    active_release_hash: str,
    candidate_release_hash: str,
    raw: Iterable[RawCandidate],
    fixtures: Iterable[ManifestEntry],
    created_at: datetime,
) -> BenchmarkManifest:
    """Freeze all fixtures and a deterministic stratified raw sample."""
    unique: dict[tuple[str, str], RawCandidate] = {}
    for item in raw:
        raw_key = (item.artifact_id, item.content_hash)
        unique.setdefault(raw_key, item)
    strata: dict[str, list[RawCandidate]] = defaultdict(list)
    for item in unique.values():
        strata[item.stratum].append(item)
    for items in strata.values():
        items.sort(
            key=lambda item: (
                item.structural_signature,
                -item.fetched_at.timestamp(),
                item.artifact_id,
            )
        )
    selected: list[RawCandidate] = []
    positions = {key: 0 for key in strata}
    while len(selected) < min(MAX_RAW_ENTRIES, len(unique)):
        progressed = False
        for stratum in sorted(strata):
            position = positions[stratum]
            if position < len(strata[stratum]) and len(selected) < MAX_RAW_ENTRIES:
                selected.append(strata[stratum][position])
                positions[stratum] += 1
                progressed = True
        if not progressed:
            break
    fixture_entries = tuple(sorted(fixtures, key=lambda item: item.entry_id))
    if any(entry.kind != "fixture" for entry in fixture_entries):
        raise ValueError("fixture manifest entry required")
    raw_entries = tuple(
        ManifestEntry(
            item.artifact_id,
            "artifact",
            item.content_hash,
            item.variant,
            item.stratum,
            item.expires_at,
        )
        for item in selected
    )
    selected_counts = tuple(
        (stratum, sum(item.stratum == stratum for item in selected))
        for stratum in sorted(strata)
    )
    eligible_counts = tuple(
        (stratum, len(strata[stratum])) for stratum in sorted(strata)
    )
    identity = {
        "source": source,
        "active": active_release_hash,
        "candidate": candidate_release_hash,
        "entries": [entry.__dict__ for entry in (*raw_entries, *fixture_entries)],
        "created_at": created_at.isoformat(),
        "policy": SELECTION_POLICY,
    }
    manifest_id = hashlib.sha256(
        json.dumps(
            identity, sort_keys=True, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return BenchmarkManifest(
        manifest_id,
        source,
        active_release_hash,
        candidate_release_hash,
        (*raw_entries, *fixture_entries),
        created_at,
        SELECTION_POLICY,
        eligible_counts,
        selected_counts,
    )


class InputReader(Protocol):
    def __call__(self, entry: ManifestEntry) -> PageInput: ...


class Parser(Protocol):
    def __call__(self, page: PageInput, release_hash: str) -> ParserResult: ...


@dataclass(frozen=True)
class BenchmarkEntryResult:
    entry_id: str
    kind: str
    variant: str
    status: Literal["compared", "benchmark-input-unavailable"]
    active: ParserResult | None
    candidate: ParserResult | None
    expires_at: datetime | None


@dataclass(frozen=True)
class BenchmarkProgress:
    processed: int
    total: int
    source: Portal
    variant: str
    unavailable_inputs: int


@dataclass(frozen=True)
class BenchmarkWorkerLimits:
    instances: int = 1
    cpu_cores: float = 0.5
    memory_mib: int = 256
    io_weight: int = 100
    database_connections: int = 1
    nice: int = 10

    def __post_init__(self) -> None:
        if not (
            self.instances == 1
            and 0 < self.cpu_cores <= 1
            and 64 <= self.memory_mib <= 512
            and 1 <= self.io_weight <= 1_000
            and self.database_connections == 1
            and 1 <= self.nice <= 19
        ):
            raise ValueError("benchmark worker must remain independently bounded")


class BenchmarkWorker:
    """Runs exactly two parser callables over the same frozen in-memory bytes."""

    def __init__(
        self,
        read_input: InputReader,
        baseline: Parser,
        candidate: Parser,
        progress: Callable[[BenchmarkProgress], None] = lambda event: None,
    ) -> None:
        self._read = read_input
        self._baseline = baseline
        self._candidate = candidate
        self._progress = progress

    def run(self, manifest: BenchmarkManifest) -> tuple[BenchmarkEntryResult, ...]:
        results = []
        unavailable = 0
        for position, entry in enumerate(manifest.entries, start=1):
            try:
                page = self._read(entry)
            except (FileNotFoundError, ValueError):
                unavailable += 1
                result = BenchmarkEntryResult(
                    entry.entry_id,
                    entry.kind,
                    entry.variant,
                    "benchmark-input-unavailable",
                    None,
                    None,
                    entry.expires_at,
                )
            else:
                result = BenchmarkEntryResult(
                    entry.entry_id,
                    entry.kind,
                    entry.variant,
                    "compared",
                    self._baseline(page, manifest.active_release_hash),
                    self._candidate(page, manifest.candidate_release_hash),
                    entry.expires_at,
                )
            results.append(result)
            self._progress(
                BenchmarkProgress(
                    position,
                    len(manifest.entries),
                    manifest.source,
                    entry.variant,
                    unavailable,
                )
            )
        return tuple(results)


ReviewState = Literal["unreviewed", "correct", "incorrect", "ambiguous"]


@dataclass(frozen=True)
class DifferenceReview:
    signature: str
    state: ReviewState
    candidate_adds_value: bool
    reason: str


def eligible_for_activation(
    *,
    results: Iterable[BenchmarkEntryResult],
    reviews: Iterable[DifferenceReview],
    changed_variants: frozenset[str],
    evaluated_at: datetime,
) -> bool:
    """Fail closed on missing raw coverage or unresolved/unsafe differences."""
    compared_raw = {
        item.variant
        for item in results
        if item.kind == "artifact"
        and item.status == "compared"
        and item.expires_at is not None
        and item.expires_at > evaluated_at
    }
    if not changed_variants <= compared_raw:
        return False
    for review in reviews:
        if review.state in {"unreviewed", "incorrect"}:
            return False
        if review.state == "ambiguous" and review.candidate_adds_value:
            return False
    return True


__all__ = [
    "BenchmarkEntryResult",
    "BenchmarkManifest",
    "BenchmarkProgress",
    "BenchmarkWorker",
    "BenchmarkWorkerLimits",
    "DifferenceReview",
    "ManifestEntry",
    "RawCandidate",
    "build_manifest",
    "eligible_for_activation",
]
