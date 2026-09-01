"""Validate that a release JUnit report ran completely without skips."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


def validate_junit(path: Path, *, minimum_tests: int) -> int:
    if minimum_tests < 1:
        raise ValueError("minimum_tests must be positive")
    root = ET.parse(path).getroot()  # noqa: S314 - locally generated JUnit only
    suites = [root] if "tests" in root.attrib else list(root.findall("testsuite"))
    totals = {
        name: sum(int(suite.attrib.get(name, "0")) for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    }
    if (
        totals["tests"] < minimum_tests
        or totals["failures"]
        or totals["errors"]
        or totals["skipped"]
    ):
        raise ValueError(
            "release test report is incomplete: "
            f"tests={totals['tests']} failures={totals['failures']} "
            f"errors={totals['errors']} skipped={totals['skipped']}"
        )
    return totals["tests"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--minimum-tests", type=int, required=True)
    args = parser.parse_args()
    print(validate_junit(args.report, minimum_tests=args.minimum_tests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
