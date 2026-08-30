import argparse
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.catalog.orm import Base
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.sources.sample_portal import SamplePortalAlertParser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="homefinder")
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser(
        "preview", help="ingest an .eml fixture and render HTML"
    )
    preview.add_argument("fixture", type=Path)
    preview.add_argument("--database-url", default="sqlite:///homefinder-preview.db")
    preview.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "preview":
        return 2

    engine = create_engine(args.database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        result = AlertIngestionService(
            parser=SamplePortalAlertParser(),
            catalog=SqlAlchemyCatalogRepository(session),
        ).ingest(args.fixture.read_bytes())
    if args.output is None:
        print(result.preview_html)
    else:
        args.output.write_text(result.preview_html, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
