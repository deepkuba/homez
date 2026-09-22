"""Minimal container heartbeat probe without importing the application CLI."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from homefinder.runtime import heartbeat_is_fresh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a runtime heartbeat")
    parser.add_argument("--heartbeat-file", required=True, type=Path)
    parser.add_argument("--max-age-seconds", required=True, type=float)
    args = parser.parse_args(argv)
    return int(
        not heartbeat_is_fresh(
            args.heartbeat_file,
            now=datetime.now(timezone.utc),
            max_age=timedelta(seconds=args.max_age_seconds),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
