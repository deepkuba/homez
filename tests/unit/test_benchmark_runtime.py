import hashlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from homefinder.benchmark import ManifestEntry, build_manifest
from homefinder.benchmark.runtime import run_frozen_benchmark
from homefinder.parsers.contracts import PageInput, ParserResult

NOW = datetime.now(timezone.utc)
BODY = b"synthetic benchmark input"


def inputs():
    entry = ManifestEntry(
        "fixture", "fixture", hashlib.sha256(BODY).hexdigest(), "baseline", "fixture"
    )
    manifest = build_manifest(
        source="gratka",
        active_release_hash="a" * 64,
        candidate_release_hash="b" * 64,
        raw=(),
        fixtures=(entry,),
        created_at=NOW,
    )
    return manifest, PageInput(uuid4(), NOW, BODY)


def test_runtime_checks_content_before_either_parser(tmp_path):
    manifest, page = inputs()
    calls = []
    results = run_frozen_benchmark(
        manifest,
        read_input=lambda entry: PageInput(page.capture_id, NOW, b"tampered"),
        baseline=lambda page, release: calls.append(release),
        candidate=lambda page, release: calls.append(release),
        lock_file=tmp_path / "lock",
        clock=lambda: NOW,
    )
    assert calls == []
    assert results[0].status == "benchmark-input-unavailable"


def test_runtime_rejects_wrong_parser_identity(tmp_path):
    manifest, page = inputs()

    def parser(page, release):
        return ParserResult(page.capture_id, "c" * 64, "baseline", (), ())

    with pytest.raises(ValueError, match="identity"):
        run_frozen_benchmark(
            manifest,
            read_input=lambda entry: page,
            baseline=parser,
            candidate=parser,
            lock_file=tmp_path / "lock",
            clock=lambda: NOW,
        )


def test_runtime_compares_identical_verified_input_and_reports_progress(tmp_path):
    manifest, page = inputs()
    seen, progress = [], []

    def parser(actual, release):
        seen.append(actual)
        return ParserResult(actual.capture_id, release, "baseline", (), ())

    results = run_frozen_benchmark(
        manifest,
        read_input=lambda entry: page,
        baseline=parser,
        candidate=parser,
        lock_file=tmp_path / "lock",
        clock=lambda: NOW,
        progress=progress.append,
    )
    assert seen == [page, page]
    assert results[0].status == "compared"
    assert progress[-1].processed == 1


def test_runtime_excludes_concurrent_runs(tmp_path):
    import fcntl

    manifest, page = inputs()
    with (tmp_path / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="already running"):
            run_frozen_benchmark(
                manifest,
                read_input=lambda entry: page,
                baseline=lambda p, r: None,
                candidate=lambda p, r: None,
                lock_file=tmp_path / "lock",
                clock=lambda: NOW,
            )


def test_fixture_rehearsal_cli_runs_packaged_parser_without_detail_output(
    tmp_path, capsys
):
    import json
    from dataclasses import asdict
    from pathlib import Path

    from homefinder.benchmark.rehearsal import main
    from homefinder.parser_releases.package import load_packaged_parser

    packaged = load_packaged_parser(
        "gratka", dependency_lock_file=Path("requirements.lock")
    )
    manifest, _ = inputs()
    manifest = build_manifest(
        source="gratka",
        active_release_hash=packaged.release_hash,
        candidate_release_hash=packaged.release_hash,
        raw=(),
        fixtures=manifest.entries,
        created_at=NOW,
    )
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(asdict(manifest), default=str))
    (tmp_path / manifest.entries[0].content_hash).write_bytes(BODY)
    assert (
        main(
            [
                "--manifest",
                str(manifest_file),
                "--fixtures",
                str(tmp_path),
                "--dependency-lock-file",
                "requirements.lock",
                "--lock-file",
                str(tmp_path / "lock"),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "processed" in output
    assert "synthetic benchmark input" not in output


def test_runtime_rejects_mutated_frozen_manifest(tmp_path):
    from dataclasses import replace

    manifest, page = inputs()
    with pytest.raises(ValueError, match="manifest identity"):
        run_frozen_benchmark(
            replace(manifest, candidate_release_hash="c" * 64),
            read_input=lambda entry: page,
            baseline=lambda p, r: None,
            candidate=lambda p, r: None,
            lock_file=tmp_path / "lock",
        )


def test_runtime_does_not_read_expired_artifact(tmp_path):
    from datetime import timedelta

    from homefinder.benchmark import RawCandidate

    manifest, page = inputs()
    raw = RawCandidate(
        "expired",
        page.capture_id,
        hashlib.sha256(BODY).hexdigest(),
        NOW,
        "baseline",
        "baseline",
        "shape",
        NOW - timedelta(seconds=1),
    )
    manifest = build_manifest(
        source="gratka",
        active_release_hash="a" * 64,
        candidate_release_hash="b" * 64,
        raw=(raw,),
        fixtures=(),
        created_at=NOW,
    )
    reads = []
    results = run_frozen_benchmark(
        manifest,
        read_input=lambda entry: reads.append(entry),
        baseline=lambda p, r: None,
        candidate=lambda p, r: None,
        lock_file=tmp_path / "lock",
        clock=lambda: NOW,
    )
    assert reads == []
    assert results[0].status == "benchmark-input-unavailable"
