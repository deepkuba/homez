import inspect
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from homefinder.benchmark import (
    BenchmarkWorker,
    BenchmarkWorkerLimits,
    DifferenceReview,
    ManifestEntry,
    RawCandidate,
    build_manifest,
    eligible_for_activation,
)
from homefinder.parsers.contracts import PageFacts, PageInput, ParserResult

NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def test_benchmark_worker_cannot_construct_network_transport() -> None:
    from homefinder.benchmark import BenchmarkWorker

    parameters = inspect.signature(BenchmarkWorker).parameters

    assert not ({"transport", "network", "client", "proxy"} & parameters.keys())
    assert not hasattr(BenchmarkWorker, "fetch")
    assert BenchmarkWorkerLimits() == BenchmarkWorkerLimits(
        instances=1,
        cpu_cores=0.5,
        memory_mib=256,
        io_weight=100,
        database_connections=1,
        nice=10,
    )


def test_manifest_selection_is_deterministic_stratified_and_bounded() -> None:
    raw = [
        RawCandidate(
            f"artifact-{index}",
            uuid4(),
            f"{index:064x}",
            NOW - timedelta(minutes=index),
            "baseline" if index % 2 else "rare",
            f"cluster-{index % 3}",
            f"shape-{index % 7}",
            NOW + timedelta(days=30),
        )
        for index in range(1_020)
    ]
    fixture = ManifestEntry("fixture-v1", "fixture", "f" * 64, "baseline", "fixture")

    first = build_manifest(
        source="gratka",
        active_release_hash="a" * 64,
        candidate_release_hash="b" * 64,
        raw=reversed(raw),
        fixtures=(fixture,),
        created_at=NOW,
    )
    second = build_manifest(
        source="gratka",
        active_release_hash="a" * 64,
        candidate_release_hash="b" * 64,
        raw=raw,
        fixtures=(fixture,),
        created_at=NOW,
    )

    assert first == second
    assert len(first.artifact_ids) == 1_000
    assert first.entries[-1] == fixture
    assert (
        dict(first.selected_by_stratum).keys() == dict(first.eligible_by_stratum).keys()
    )


def test_worker_uses_identical_bytes_and_records_unavailable_input() -> None:
    capture = uuid4()
    entries = (
        ManifestEntry("available", "artifact", "a" * 64, "baseline", "positive"),
        ManifestEntry("missing", "artifact", "b" * 64, "rare", "failure"),
    )
    manifest = build_manifest(
        source="gratka",
        active_release_hash="a" * 64,
        candidate_release_hash="b" * 64,
        raw=(),
        fixtures=(),
        created_at=NOW,
    )
    manifest = manifest.__class__(
        manifest.manifest_id,
        manifest.source,
        manifest.active_release_hash,
        manifest.candidate_release_hash,
        entries,
        manifest.created_at,
        manifest.selection_policy,
        (),
        (),
    )
    seen = []
    progress = []

    def read(entry):
        if entry.entry_id == "missing":
            raise FileNotFoundError
        return PageInput(capture, NOW, b"frozen synthetic bytes")

    def parse(page, release):
        seen.append((page.body, release))
        return ParserResult(capture, release, "baseline", (), (), facts=PageFacts())

    results = BenchmarkWorker(read, parse, parse, progress.append).run(manifest)

    assert seen == [
        (b"frozen synthetic bytes", "a" * 64),
        (b"frozen synthetic bytes", "b" * 64),
    ]
    assert [item.status for item in results] == [
        "compared",
        "benchmark-input-unavailable",
    ]
    assert (
        progress[-1].processed,
        progress[-1].total,
        progress[-1].unavailable_inputs,
    ) == (
        2,
        2,
        1,
    )


def test_eligibility_requires_raw_variants_and_safe_reviews() -> None:
    capture = uuid4()
    result = ParserResult(capture, "a" * 64, "rare", (), (), facts=PageFacts())
    from homefinder.benchmark import BenchmarkEntryResult

    compared = BenchmarkEntryResult(
        "artifact-a",
        "artifact",
        "rare",
        "compared",
        result,
        result,
        NOW + timedelta(days=1),
    )

    assert eligible_for_activation(
        results=(compared,),
        reviews=(DifferenceReview("diff", "correct", True, "reviewed"),),
        changed_variants=frozenset({"rare"}),
        evaluated_at=NOW,
    )
    assert not eligible_for_activation(
        results=(compared,),
        reviews=(DifferenceReview("diff", "ambiguous", True, "uncertain"),),
        changed_variants=frozenset({"rare"}),
        evaluated_at=NOW,
    )
    assert not eligible_for_activation(
        results=(compared,),
        reviews=(),
        changed_variants=frozenset({"other"}),
        evaluated_at=NOW,
    )


@pytest.mark.parametrize("state", ["unreviewed", "incorrect"])
def test_unreviewed_and_incorrect_differences_block(state) -> None:
    from homefinder.benchmark import BenchmarkEntryResult

    capture = uuid4()
    result = ParserResult(capture, "a" * 64, "baseline", (), (), facts=PageFacts())
    compared = BenchmarkEntryResult(
        "artifact-a",
        "artifact",
        "baseline",
        "compared",
        result,
        result,
        NOW + timedelta(days=1),
    )
    assert not eligible_for_activation(
        results=(compared,),
        reviews=(DifferenceReview("diff", state, False, "review pending"),),
        changed_variants=frozenset({"baseline"}),
        evaluated_at=NOW,
    )
