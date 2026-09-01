import argparse
import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from threading import Event
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.application.gmail_labels import GmailLabelManager
from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.application.poll_gmail import GmailPollingService, PollResult
from homefinder.catalog.orm import Base, ReportDraftRecord, ReportItemRecord
from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.config import Environment, Settings
from homefinder.digest.delivery import (
    DeliveryOutbox,
    DeliveryWorker,
    FridayScheduler,
    HttpMailTransport,
)
from homefinder.digest.feedback import SqlAlchemyFeedbackService, private_feedback_url
from homefinder.operations.backup import (
    backup_database,
    load_backup_key,
    prune_backups,
    restore_database,
)
from homefinder.runtime import (
    heartbeat_is_fresh,
    install_stop_signals,
    run_periodically,
)
from homefinder.sources.gmail import (
    EncryptedTokenStore,
    GmailApiClient,
    GoogleOAuthRefreshClient,
    RefreshableOAuthTokenProvider,
    load_encryption_key,
    read_secret_text,
)
from homefinder.sources.policy import SourcePolicy, SourcePolicyRegistry
from homefinder.sources.portal_alerts import (
    GratkaAlertParser,
    MorizonAlertParser,
    OtodomAlertParser,
    SanitizedPortalAlertParser,
)
from homefinder.sources.sample_portal import SamplePortalAlertParser
from homefinder.workflow.service import WorkflowService


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
    restore = commands.add_parser(
        "restore", help="restore an encrypted PostgreSQL backup"
    )
    restore.add_argument("backup", type=Path)
    prune = commands.add_parser(
        "prune-backups", help="remove expired encrypted backups"
    )
    prune.add_argument("directory", type=Path)
    prune.add_argument("--keep-days", type=int, default=14)
    commands.add_parser(
        "reconcile-workflow", help="enqueue missing catalog workflow stages"
    )
    commands.add_parser("workflow-status", help="show durable workflow state counts")
    worker = commands.add_parser("workflow-worker", help="run durable workflow jobs")
    worker.add_argument("--worker-id", required=True)
    worker.add_argument("--max-jobs", type=int, default=100)
    report = commands.add_parser(
        "enqueue-report", help="enqueue deterministic report preparation"
    )
    report.add_argument("--period", required=True)
    report.add_argument("--cutoff-at", required=True)
    report.add_argument("--routing-goal-version", type=int, default=1)
    enqueue_poll = commands.add_parser(
        "enqueue-poll", help="enqueue an idempotent source polling slot"
    )
    enqueue_poll.add_argument(
        "--source", required=True, choices=("otodom", "morizon", "gratka")
    )
    enqueue_poll.add_argument("--scheduled-at", required=True)
    schedule = commands.add_parser(
        "schedule-delivery", help="enqueue the most recent due Friday report"
    )
    schedule.add_argument("--now")
    delivery = commands.add_parser(
        "delivery-worker", help="send claimed delivery outbox records"
    )
    delivery.add_argument("--max-deliveries", type=int, default=10)
    runtime_workflow = commands.add_parser(
        "runtime-workflow", help="run the persistent workflow worker"
    )
    runtime_workflow.add_argument("--worker-id", required=True)
    runtime_workflow.add_argument("--interval-seconds", type=float, default=5)
    runtime_workflow.add_argument("--max-jobs", type=int, default=100)
    runtime_workflow.add_argument("--heartbeat-file", required=True, type=Path)
    runtime_workflow.add_argument("--once", action="store_true")
    runtime_scheduler = commands.add_parser(
        "runtime-scheduler", help="enqueue idempotent periodic workflow work"
    )
    runtime_scheduler.add_argument("--interval-seconds", type=float, default=60)
    runtime_scheduler.add_argument("--heartbeat-file", required=True, type=Path)
    runtime_scheduler.add_argument("--once", action="store_true")
    runtime_delivery = commands.add_parser(
        "runtime-delivery", help="run the persistent delivery worker"
    )
    runtime_delivery.add_argument("--interval-seconds", type=float, default=5)
    runtime_delivery.add_argument("--max-deliveries", type=int, default=10)
    runtime_delivery.add_argument("--heartbeat-file", required=True, type=Path)
    runtime_delivery.add_argument("--once", action="store_true")
    runtime_health = commands.add_parser(
        "runtime-health", help="check a container runtime heartbeat"
    )
    runtime_health.add_argument("--heartbeat-file", required=True, type=Path)
    runtime_health.add_argument("--max-age-seconds", type=float, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"backup", "restore"}:
        settings = Settings()
        if settings.backup_key_file is None:
            raise SystemExit("backup key secret file is required")
        key = load_backup_key(settings.backup_key_file)
        database_url = settings.database_url.get_secret_value()
    if args.command == "backup":
        backup_database(
            None,
            args.destination,
            key,
            database_url=database_url,
        )
        return 0
    if args.command == "restore":
        restore_database(
            args.backup,
            key,
            database_url=database_url,
        )
        return 0
    if args.command == "prune-backups":
        for path in prune_backups(args.directory, keep_days=args.keep_days):
            print(path)
        return 0
    if args.command == "runtime-health":
        return not heartbeat_is_fresh(
            args.heartbeat_file,
            now=datetime.now(timezone.utc),
            max_age=timedelta(seconds=args.max_age_seconds),
        )
    if args.command.startswith("runtime-"):
        _run_container_runtime(Settings(), args)
        return 0
    if args.command in {"schedule-delivery", "delivery-worker"}:
        settings = Settings()
        engine = create_engine(settings.database_url.get_secret_value())
        sessions = sessionmaker(engine, expire_on_commit=False)
        outbox = DeliveryOutbox(sessions)
        now = (
            _parse_datetime(args.now)
            if getattr(args, "now", None)
            else datetime.now(timezone.utc)
        )
        if args.command == "schedule-delivery":
            if settings.report_recipient_file is None:
                raise SystemExit("report recipient secret file is required")
            period = FridayScheduler.most_recent_due_period(now)
            with sessions() as session:
                report = session.scalar(
                    select(ReportDraftRecord)
                    .where(
                        ReportDraftRecord.period == period,
                        ReportDraftRecord.status == "prepared",
                    )
                    .order_by(ReportDraftRecord.prepared_at.desc())
                )
            if report is None:
                raise SystemExit("no prepared report exists for the due period")
            print(
                outbox.enqueue(
                    period=period,
                    report_id=str(report.id),
                    recipient=read_secret_text(settings.report_recipient_file),
                    render_version=report.render_version,
                    now=now,
                )
            )
        else:
            worker = DeliveryWorker(
                sessions,
                outbox,
                _mail_transport(settings),
                feedback_links=_feedback_links(settings, sessions),
            )
            delivered = 0
            while delivered < args.max_deliveries and worker.run_once(now=now):
                delivered += 1
            print(delivered)
        return 0
    if args.command in {
        "reconcile-workflow",
        "workflow-status",
        "workflow-worker",
        "enqueue-report",
        "enqueue-poll",
    }:
        settings = Settings()
        engine = create_engine(settings.database_url.get_secret_value())
        workflow = WorkflowService(
            sessionmaker(engine, expire_on_commit=False),
            pollers=(
                _gmail_pollers(settings) if args.command == "workflow-worker" else None
            ),
        )
        now = datetime.now(timezone.utc)
        if args.command == "reconcile-workflow":
            print(workflow.reconcile_catalog(now=now))
        elif args.command == "workflow-status":
            print(json.dumps(workflow.jobs.status(), sort_keys=True))
        elif args.command == "workflow-worker":
            print(
                workflow.run_until_idle(
                    worker_id=args.worker_id, now=now, max_jobs=args.max_jobs
                )
            )
        elif args.command == "enqueue-report":
            cutoff = _parse_datetime(args.cutoff_at)
            print(
                workflow.enqueue_report(
                    period=args.period,
                    cutoff_at=cutoff,
                    routing_goal_version=args.routing_goal_version,
                )
            )
        else:
            print(
                workflow.enqueue_poll(
                    source_key=args.source,
                    scheduled_at=_parse_datetime(args.scheduled_at),
                )
            )
        return 0
    if args.command == "poll-gmail":
        print(_poll_gmail(Settings(), args.source))
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


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise SystemExit("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise SystemExit("timestamp must include an explicit timezone")
    return parsed


def _poll_gmail(settings: Settings, source_key: str) -> PollResult:
    if settings.environment is not Environment.PRODUCTION:
        raise SystemExit("Gmail polling requires HOMEFINDER_ENVIRONMENT=production")
    if (
        settings.gmail_token_file is None
        or settings.gmail_token_key_file is None
        or settings.gmail_source_policy_file is None
    ):
        raise SystemExit("Gmail secret and policy file paths are required")
    policy = _load_source_policy(settings.gmail_source_policy_file, source_key)
    parser = _portal_parser(source_key, policy)
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
            mailbox_key=settings.gmail_mailbox_key, source_key=source_key
        )
        return GmailPollingService(
            session=session,
            gmail=gmail,
            ingestion=AlertIngestionService(
                parser=parser,
                catalog=SqlAlchemyCatalogRepository(session),
            ),
            policies=SourcePolicyRegistry((policy,)),
            source_key=source_key,
            mailbox_key=settings.gmail_mailbox_key,
            labels=labels,
        ).poll()


def _gmail_pollers(settings: Settings) -> dict[str, Callable[[], object]]:
    return {
        source: partial(_poll_gmail, settings, source)
        for source in ("otodom", "morizon", "gratka")
    }


def _feedback_links(
    settings: Settings,
    sessions: sessionmaker[Session],
) -> Callable[[str, datetime], tuple[tuple[str, str], ...]]:
    if settings.feedback_base_url is None or settings.feedback_token_key_file is None:
        raise SystemExit("feedback URL and signing-key file are required")
    base_url = settings.feedback_base_url
    signing_key = read_secret_text(settings.feedback_token_key_file).encode()
    service = SqlAlchemyFeedbackService(sessions)

    def issue(report_id: str, now: datetime) -> tuple[tuple[str, str], ...]:
        with sessions() as session:
            items = session.scalars(
                select(ReportItemRecord)
                .where(ReportItemRecord.report_id == UUID(report_id))
                .order_by(ReportItemRecord.section, ReportItemRecord.position)
            ).all()
        links = []
        for item in items:
            listing_id = str(item.listing_id)
            token = service.issue_stable(
                report_id,
                listing_id,
                now=now,
                ttl=timedelta(days=7),
                signing_key=signing_key,
            )
            label = f"{item.section.title()} home {item.position + 1}"
            links.append(
                (
                    label,
                    private_feedback_url(
                        base_url,
                        report_id=report_id,
                        listing_id=listing_id,
                        token=token,
                    ),
                )
            )
        return tuple(links)

    return issue


def _mail_transport(settings: Settings) -> HttpMailTransport:
    if (
        settings.mail_api_token_file is None
        or settings.mail_api_endpoint is None
        or settings.mail_api_host is None
        or settings.mail_sender is None
    ):
        raise SystemExit("mail provider settings and secret file are required")
    return HttpMailTransport(
        endpoint=settings.mail_api_endpoint,
        allowed_host=settings.mail_api_host,
        token_file=settings.mail_api_token_file,
        sender=settings.mail_sender,
    )


def _run_container_runtime(settings: Settings, args: argparse.Namespace) -> None:
    engine = create_engine(settings.database_url.get_secret_value())
    sessions = sessionmaker(engine, expire_on_commit=False)
    if args.command == "runtime-workflow":
        workflow = WorkflowService(sessions, pollers=_gmail_pollers(settings))

        def action(now: datetime) -> None:
            workflow.reconcile_catalog(now=now)
            workflow.run_until_idle(
                worker_id=args.worker_id, now=now, max_jobs=args.max_jobs
            )

    elif args.command == "runtime-scheduler":
        workflow = WorkflowService(sessions)
        outbox = DeliveryOutbox(sessions)

        def action(now: datetime) -> None:
            for source in ("otodom", "morizon", "gratka"):
                workflow.enqueue_poll(source_key=source, scheduled_at=now)
            workflow.reconcile_catalog(now=now)
            period = FridayScheduler.most_recent_due_period(now)
            scheduled_at = FridayScheduler.scheduled_at(period)
            workflow.enqueue_report(
                period=period,
                cutoff_at=scheduled_at,
                routing_goal_version=1,
            )
            if settings.report_recipient_file is None:
                raise RuntimeError("report recipient secret file is required")
            with sessions() as session:
                report = session.scalar(
                    select(ReportDraftRecord)
                    .where(
                        ReportDraftRecord.period == period,
                        ReportDraftRecord.status == "prepared",
                    )
                    .order_by(ReportDraftRecord.prepared_at.desc())
                )
            if report is not None:
                outbox.enqueue(
                    period=period,
                    report_id=str(report.id),
                    recipient=read_secret_text(settings.report_recipient_file),
                    render_version=report.render_version,
                    now=now,
                )

    else:
        outbox = DeliveryOutbox(sessions)
        worker = DeliveryWorker(
            sessions,
            outbox,
            _mail_transport(settings),
            feedback_links=_feedback_links(settings, sessions),
        )

        def action(now: datetime) -> None:
            delivered = 0
            while delivered < args.max_deliveries and worker.run_once(now=now):
                delivered += 1

    stop = Event()
    install_stop_signals(stop)
    run_periodically(
        action,
        interval_seconds=args.interval_seconds,
        heartbeat_file=args.heartbeat_file,
        stop=stop,
        max_iterations=1 if args.once else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
