"""Thin no-cache client for safe metadata and one scoped raw stream."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="homez-artifacts", allow_abbrev=False)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    metadata = commands.add_parser("metadata")
    metadata.add_argument("--cursor")
    metadata.add_argument("--limit", type=int, default=50)
    raw = commands.add_parser("raw-stream")
    raw.add_argument("artifact_id")
    raw.add_argument("--purpose", required=True)
    args = parser.parse_args(argv)
    if urlparse(args.base_url).scheme != "https":
        raise SystemExit("maintenance API requires HTTPS")
    token = args.token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("empty token file")
    if args.command == "metadata":
        query = {"limit": args.limit}
        if args.cursor:
            query["cursor"] = args.cursor
        path = "/metadata?" + urlencode(query)
    else:
        path = f"/raw/{args.artifact_id}?" + urlencode({"purpose": args.purpose})
    request = Request(  # noqa: S310 - HTTPS scheme checked above
        args.base_url.rstrip("/") + path,
        headers={"Authorization": f"Bearer {token}", "Cache-Control": "no-store"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - operator URL
        while chunk := response.read(64 * 1024):
            sys.stdout.buffer.write(chunk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
