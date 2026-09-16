"""Local synthetic-fixture rehearsal; never produces activation evidence."""

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter

from homefinder.benchmark import BenchmarkManifest, BenchmarkProgress, ManifestEntry
from homefinder.benchmark.runtime import run_frozen_benchmark, validate_manifest
from homefinder.parser_releases.package import load_packaged_parser
from homefinder.parsers.contracts import MAX_PAGE_BYTES, PageInput, ParserResult


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--dependency-lock-file", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path, required=True)
    args = parser.parse_args(argv)
    with args.manifest.open("rb") as source:
        data = source.read(MAX_PAGE_BYTES + 1)
    if len(data) > MAX_PAGE_BYTES:
        raise ValueError("benchmark manifest exceeds size limit")
    manifest = TypeAdapter(BenchmarkManifest).validate_json(data)
    validate_manifest(manifest)
    if any(entry.kind != "fixture" for entry in manifest.entries):
        raise ValueError("rehearsal accepts synthetic fixtures only")
    packaged = load_packaged_parser(
        manifest.source, dependency_lock_file=args.dependency_lock_file
    )
    if (
        manifest.active_release_hash != packaged.release_hash
        or manifest.candidate_release_hash != packaged.release_hash
    ):
        raise ValueError(
            "rehearsal requires the installed parser identity on both sides"
        )

    def read(entry: ManifestEntry) -> PageInput:
        descriptor = os.open(
            args.fixtures / entry.content_hash, os.O_RDONLY | os.O_NOFOLLOW
        )
        with os.fdopen(descriptor, "rb") as fixture:
            body = fixture.read(MAX_PAGE_BYTES + 1)
        return PageInput(
            uuid5(NAMESPACE_URL, manifest.manifest_id + ":" + entry.entry_id),
            manifest.created_at,
            body,
        )

    def parse(page: PageInput, release_hash: str) -> ParserResult:
        return packaged.parser.parse(page)

    def report(event: BenchmarkProgress) -> None:
        print(json.dumps(asdict(event), sort_keys=True), flush=True)

    results = run_frozen_benchmark(
        manifest,
        read_input=read,
        baseline=parse,
        candidate=parse,
        lock_file=args.lock_file,
        progress=report,
    )
    return int(any(item.status != "compared" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
