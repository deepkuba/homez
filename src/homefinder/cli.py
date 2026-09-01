import argparse
import base64
import json
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from homefinder.application.gmail_labels import GmailLabelManager
from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.application.poll_gmail import GmailPollingService
from homefinder.catalog.orm import Base
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.config import Environment, Settings
from homefinder.operations.backup import (
    backup_database,
    prune_backups,
    restore_database,
)
from homefinder.sources.gmail import (
    EncryptedTokenStore,
    GmailApiClient,
    GoogleOAuthRefreshClient,
    RefreshableOAuthTokenProvider,
    load_encryption_key,
)
from homefinder.sources.policy import SourcePolicy, SourcePolicyRegistry
from homefinder.sources.portal_alerts import (
    GratkaAlertParser,
    MorizonAlertParser,
    OtodomAlertParser,
    SanitizedPortalAlertParser,
)
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
    poll.add_argument(
        "--source", required=True, choices=("otodom", "morizon", "gratka")
    )
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
    if args.command == "poll-gmail":
        settings = Settings()
        if settings.environment is not Environment.PRODUCTION:
            raise SystemExit("poll-gmail requires HOMEFINDER_ENVIRONMENT=production")
        if (
            settings.gmail_token_file is None
            or settings.gmail_token_key_file is None
            or settings.gmail_source_policy_file is None
        ):
            raise SystemExit("Gmail secret and policy file paths are required")
        policy = _load_source_policy(settings.gmail_source_policy_file, args.source)
        parser = _portal_parser(args.source, policy)
        engine = create_engine(settings.database_url.get_secret_value())
        store = EncryptedTokenStore(load_encryption_key(settings.gmail_token_key_file))
        provider = RefreshableOAuthTokenProvider(
            store=store,
            token_path=settings.gmail_token_file,
            refresh_client=GoogleOAuthRefreshClient(),
        )
        with Session(engine) as session:
            gmail = GmailApiClient(provider)
            labels = GmailLabelManager(session, gmail).resolve(
                mailbox_key=settings.gmail_mailbox_key, source_key=args.source
            )
            poll_result = GmailPollingService(
                session=session,
                gmail=gmail,
                ingestion=AlertIngestionService(
                    parser=parser,
                    catalog=SqlAlchemyCatalogRepository(session),
                ),
                policies=SourcePolicyRegistry((policy,)),
                source_key=args.source,
                mailbox_key=settings.gmail_mailbox_key,
                labels=labels,
            ).poll()
        print(poll_result)
        return 0
    engine = create_engine(args.database_url)
    Base.metadata.create_all(engine)
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


def _portal_parser(source_key: str, policy: SourcePolicy) -> SanitizedPortalAlertParser:
    parsers: dict[str, type[SanitizedPortalAlertParser]] = {
        "otodom": OtodomAlertParser,
        "morizon": MorizonAlertParser,
        "gratka": GratkaAlertParser,
    }
    if len(policy.allowed_senders) != 1 or len(policy.allowed_hosts) != 1:
        raise SystemExit(
            "each parser contract requires one sender and one listing host"
        )
    parser = parsers[source_key]()
    parser.expected_sender = next(iter(policy.allowed_senders))
    parser.expected_host = next(iter(policy.allowed_hosts))
    return parser


def _load_source_policy(path: Path, source_key: str) -> SourcePolicy:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        source = payload["sources"][source_key]
        if source.get("enabled") is not True:
            raise ValueError("source is not enabled")
        senders = frozenset(
            str(value).casefold() for value in source["allowed_senders"]
        )
        hosts = frozenset(str(value).casefold() for value in source["allowed_hosts"])
        if not senders or not hosts:
            raise ValueError("source policy allowlists cannot be empty")
        return SourcePolicy(
            key=source_key,
            allowed_senders=senders,
            allowed_hosts=hosts,
            max_message_bytes=int(source.get("max_message_bytes", 512_000)),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit("source policy file is invalid") from error


if __name__ == "__main__":
    raise SystemExit(main())
