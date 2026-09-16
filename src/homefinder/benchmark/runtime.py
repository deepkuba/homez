"""Network-free execution boundary for an explicitly supplied immutable parser pair.

The caller owns authenticated input access and benchmark-only persistence. This
module never constructs transport, database sessions, or production writers.
"""

import fcntl
import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from homefinder.benchmark import (
    MAX_RAW_ENTRIES,
    SELECTION_POLICY,
    BenchmarkEntryResult,
    BenchmarkManifest,
    BenchmarkProgress,
    BenchmarkWorker,
    InputReader,
    ManifestEntry,
    Parser,
)
from homefinder.parsers.contracts import PageInput, ParserResult


def validate_manifest(manifest: BenchmarkManifest) -> None:
    entries = manifest.entries
    if (
        manifest.source not in {"gratka", "morizon", "otodom", "olx"}
        or manifest.created_at.utcoffset() is None
        or manifest.selection_policy != SELECTION_POLICY
        or len(manifest.artifact_ids) > MAX_RAW_ENTRIES
        or len({entry.entry_id for entry in entries}) != len(entries)
        or any(entry.kind not in {"artifact", "fixture"} for entry in entries)
    ):
        raise ValueError("invalid frozen benchmark manifest")
    for digest in (
        manifest.active_release_hash,
        manifest.candidate_release_hash,
        *(entry.content_hash for entry in entries),
    ):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid benchmark content identity")
    identity = {
        "source": manifest.source,
        "active": manifest.active_release_hash,
        "candidate": manifest.candidate_release_hash,
        "entries": [entry.__dict__ for entry in entries],
        "created_at": manifest.created_at.isoformat(),
        "policy": manifest.selection_policy,
    }
    expected = hashlib.sha256(
        json.dumps(
            identity, sort_keys=True, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if manifest.manifest_id != expected:
        raise ValueError("frozen benchmark manifest identity mismatch")


def run_frozen_benchmark(
    manifest: BenchmarkManifest,
    *,
    read_input: InputReader,
    baseline: Parser,
    candidate: Parser,
    lock_file: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    progress: Callable[[BenchmarkProgress], None] = lambda event: None,
) -> tuple[BenchmarkEntryResult, ...]:
    """Compare frozen, verified inputs once under a NAS-wide advisory lock.

    Both parser implementations must come from the authorized immutable pair.
    Their returned capture/release identities are checked before results escape.
    Keep the lock file on the shared NAS runtime volume, never unlink it.
    """
    validate_manifest(manifest)

    def read(entry: ManifestEntry) -> PageInput:
        if entry.kind == "artifact" and (
            entry.expires_at is None
            or entry.expires_at.utcoffset() is None
            or entry.expires_at <= clock()
        ):
            raise ValueError("benchmark input expired")
        page = read_input(entry)
        if hashlib.sha256(page.body).hexdigest() != entry.content_hash:
            raise ValueError("benchmark input content mismatch")
        return page

    def checked(parser: Parser) -> Parser:
        def parse(page: PageInput, release_hash: str) -> ParserResult:
            result = parser(page, release_hash)
            if (
                result.capture_id != page.capture_id
                or result.release_hash != release_hash
            ):
                raise ValueError("benchmark parser identity mismatch")
            return result

        return parse

    descriptor = os.open(lock_file, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("benchmark worker already running") from exc
        return BenchmarkWorker(
            read, checked(baseline), checked(candidate), progress
        ).run(manifest)
