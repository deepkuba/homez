from pathlib import Path


def test_packaged_parser_hash_binds_source_code_config_and_dependency_lock(
    tmp_path: Path,
) -> None:
    from homefinder.parser_releases.package import load_packaged_parser
    from homefinder.parsers.gratka import GratkaPageParser

    first_lock = tmp_path / "requirements-a.lock"
    second_lock = tmp_path / "requirements-b.lock"
    first_lock.write_bytes(b"synthetic-dependency==1\n")
    second_lock.write_bytes(b"synthetic-dependency==2\n")

    first = load_packaged_parser("gratka", dependency_lock_file=first_lock)
    same = load_packaged_parser("gratka", dependency_lock_file=first_lock)
    changed = load_packaged_parser("gratka", dependency_lock_file=second_lock)

    assert isinstance(first.parser, GratkaPageParser)
    assert first.release_hash == first.parser.release_hash == same.release_hash
    assert changed.release_hash != first.release_hash
    assert len(first.release_hash) == 64


def test_packaged_hash_matches_release_registry_identity(tmp_path: Path) -> None:
    from homefinder.parser_releases import ReleaseBuild
    from homefinder.parser_releases.package import load_packaged_parser

    lock = tmp_path / "requirements.lock"
    lock.write_bytes(b"synthetic-dependency==1\n")
    package = load_packaged_parser("olx", dependency_lock_file=lock)
    build = ReleaseBuild(
        source="olx",
        parser_version="olx-v1",
        git_commit="a" * 40,
        parser_content_hash=package.parser_content_hash,
        configuration_hash=package.configuration_hash,
        dependency_lock_hash=package.dependency_lock_hash,
        deployable_digest="sha256:" + "b" * 64,
        qualifying_benchmark_run="benchmark-olx-v1",
    )

    assert build.release_hash == package.release_hash
