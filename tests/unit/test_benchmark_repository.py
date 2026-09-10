from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import inspect, select

from homefinder.benchmark import (
    BenchmarkEntryResult,
    DifferenceReview,
    RawCandidate,
    build_manifest,
)
from homefinder.benchmark_repository import BenchmarkRepository
from homefinder.catalog.orm import BenchmarkResultRecord
from homefinder.parser_releases import ParserReleaseRepository, ReleaseBuild
from homefinder.parsers.contracts import PageFacts, ParserResult

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def test_eligible_benchmark_is_persisted_outside_production_models(
    scrape_queue,
) -> None:
    queue, _snapshots, workers, sessions = scrape_queue
    releases = ParserReleaseRepository(sessions)
    candidate = releases.register(
        ReleaseBuild(
            source="gratka",
            parser_version="gratka-v2",
            git_commit="2" * 40,
            parser_content_hash="2" * 64,
            configuration_hash="3" * 64,
            dependency_lock_hash="4" * 64,
            deployable_digest="sha256:" + "5" * 64,
            qualifying_benchmark_run="benchmark-gratka-v2",
        ),
        now=NOW,
    )
    raw = RawCandidate(
        "artifact-a",
        uuid4(),
        "6" * 64,
        NOW,
        "baseline",
        "positive",
        "shape-a",
        NOW + timedelta(days=30),
    )
    manifest = build_manifest(
        source="gratka",
        active_release_hash="a" * 64,
        candidate_release_hash=candidate.release_hash,
        raw=(raw,),
        fixtures=(),
        created_at=NOW,
    )
    benchmark = BenchmarkRepository(sessions, detail_key=b"k" * 32)
    benchmark.create("benchmark-gratka-v2", manifest)
    parsed = ParserResult(
        raw.capture_id,
        candidate.release_hash,
        "baseline",
        (),
        (),
        facts=PageFacts(),
    )
    assert benchmark.complete(
        "benchmark-gratka-v2",
        (
            BenchmarkEntryResult(
                raw.artifact_id,
                "artifact",
                raw.variant,
                "compared",
                parsed,
                parsed,
                raw.expires_at,
            ),
        ),
        (DifferenceReview("7" * 64, "correct", False, "equivalent result"),),
        changed_variants=frozenset({"baseline"}),
        now=NOW,
    )
    for worker in workers:
        queue.register_worker(
            worker,
            release_hashes=("a" * 64, candidate.release_hash),
            now=NOW,
        )

    assert (
        releases.activate(
            source="gratka",
            release_hash=candidate.release_hash,
            actor="operator@example.test",
            compared_metrics="eligible benchmark reviewed",
            expected_epoch=1,
            now=NOW,
        )
        == 2
    )

    with sessions() as session:
        row = session.scalar(select(BenchmarkResultRecord))
        assert row.artifact_id == "artifact-a"
        assert row.data_classification == "non-production"
        assert row.expires_at.replace(tzinfo=timezone.utc) == raw.expires_at
        assert "baseline" not in row.candidate_result_json
        tables = set(inspect(session.connection()).get_table_names())
        assert "benchmark_results" in tables
        assert "production_parser_results" in tables
        columns = {
            item["name"]
            for item in inspect(session.connection()).get_columns("benchmark_results")
        }
        assert "namespace" not in columns

    assert benchmark.expire_artifact("artifact-a") == 1
    with sessions() as session:
        row = session.scalar(select(BenchmarkResultRecord))
        assert row.artifact_id is None
        assert row.active_result_json is None
        assert row.candidate_result_json is None
