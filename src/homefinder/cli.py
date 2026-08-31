import argparse
import base64
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.application.poll_gmail import GmailPollingService
from homefinder.catalog.orm import Base
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.operations.backup import (
    backup_database,
    prune_backups,
    restore_database,
)
from homefinder.sources.gmail import EncryptedTokenStore, GmailApiClient
from homefinder.sources.policy import SourcePolicy, SourcePolicyRegistry
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
    poll = commands.add_parser("poll-gmail", help="poll governed Gmail alerts")
    poll.add_argument("--source", default="sample_portal")
    poll.add_argument("--database-url", default="sqlite:///homefinder-preview.db")
    poll.add_argument("--token-file", type=Path, required=True)
    poll.add_argument("--encryption-key", required=True, help="base64 encoded AES key")
    poll.add_argument("--label", default="INBOX")
    backup = commands.add_parser("backup", help="create an encrypted PostgreSQL backup")
    backup.add_argument("destination", type=Path)
    backup.add_argument("--database-url", required=True)
    backup.add_argument(
        "--encryption-key", required=True, help="base64 encoded AES key"
    )
    restore = commands.add_parser(
        "restore", help="restore an encrypted PostgreSQL backup"
    )
    restore.add_argument("backup", type=Path)
    restore.add_argument("--database-url", required=True)
    restore.add_argument(
        "--encryption-key", required=True, help="base64 encoded AES key"
    )
    prune = commands.add_parser(
        "prune-backups", help="remove expired encrypted backups"
    )
    prune.add_argument("directory", type=Path)
    prune.add_argument("--keep-days", type=int, default=14)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "backup":
        backup_database(
            None,
            args.destination,
            base64.urlsafe_b64decode(args.encryption_key),
            database_url=args.database_url,
        )
        return 0
    if args.command == "restore":
        restore_database(
            args.backup,
            base64.urlsafe_b64decode(args.encryption_key),
            database_url=args.database_url,
        )
        return 0
    if args.command == "prune-backups":
        for path in prune_backups(args.directory, keep_days=args.keep_days):
            print(path)
        return 0
    engine = create_engine(args.database_url)
    Base.metadata.create_all(engine)
    if args.command == "poll-gmail":
        token = EncryptedTokenStore(base64.urlsafe_b64decode(args.encryption_key)).load(
            args.token_file
        )
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise SystemExit("encrypted OAuth token has no access_token")
        policy = SourcePolicy(
            key="sample_portal",
            allowed_senders=frozenset({"alerts@fixtures.homez.invalid"}),
            allowed_hosts=frozenset({"listings.homez.invalid"}),
        )
        with Session(engine) as session:
            poll_result = GmailPollingService(
                session=session,
                gmail=GmailApiClient(access_token),
                ingestion=AlertIngestionService(
                    parser=SamplePortalAlertParser(),
                    catalog=SqlAlchemyCatalogRepository(session),
                ),
                policies=SourcePolicyRegistry((policy,)),
                source_key=args.source,
                label_id=args.label,
            ).poll()
        print(poll_result)
        return 0
    if args.command != "preview":
        return 2

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
