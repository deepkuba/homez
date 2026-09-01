from pathlib import Path

import pytest

from homefinder.operations.release_gate import validate_junit


def test_release_gate_requires_tests_without_failures_or_skips(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuites tests="7" failures="0" errors="0" skipped="0"/>',
        encoding="utf-8",
    )

    assert validate_junit(report, minimum_tests=7) == 7


@pytest.mark.parametrize(
    "attributes",
    (
        'tests="7" failures="1" errors="0" skipped="0"',
        'tests="7" failures="0" errors="1" skipped="0"',
        'tests="7" failures="0" errors="0" skipped="1"',
        'tests="6" failures="0" errors="0" skipped="0"',
    ),
)
def test_release_gate_rejects_incomplete_junit(tmp_path: Path, attributes: str) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(f"<testsuites {attributes}/>", encoding="utf-8")

    with pytest.raises(ValueError, match="release test report"):
        validate_junit(report, minimum_tests=7)
