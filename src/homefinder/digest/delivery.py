"""Friday delivery timing and exactly-once period claims."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import DigestDeliveryRecord, ReportDraftRecord
from homefinder.sources.gmail import read_secret_text


class InMemoryDeliveryLedger:
    def __init__(self) -> None:
        self._sent: set[str] = set()
        self._lock = Lock()

    def claim(self, period: str) -> bool:
        with self._lock:
            if period in self._sent:
                return False
            self._sent.add(period)
            return True


class DigestDelivery:
    def __init__(self, ledger: InMemoryDeliveryLedger) -> None:
        self.ledger = ledger

    @staticmethod
    def is_due(at: datetime) -> bool:
        local = at.astimezone(ZoneInfo("Europe/Warsaw"))
        return local.weekday() == 4 and local.hour == 10

    def send_once(self, period: str, send: Callable[[], None]) -> bool:
        if not self.ledger.claim(period):
            return False
        try:
            send()
        except Exception:
            # A failed claim must be retryable; an acknowledged send remains unique.
            self.ledger._sent.discard(period)
            raise
        return True


WARSAW = ZoneInfo("Europe/Warsaw")


class DeliveryState(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    RETRYABLE_FAILURE = "retryable_failure"
    SENT = "sent"


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    period: str
    report_id: str
    recipient: str
    render_version: str
    claim_token: UUID
    attempt_number: int


@dataclass(frozen=True, slots=True)
class MailAcknowledgement:
    provider_message_id: str
    acknowledged_at: datetime


class MailTransport:
    def send(
        self,
        *,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str,
        idempotency_key: str,
    ) -> MailAcknowledgement:
        raise NotImplementedError


class DeliveryOutbox:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def enqueue(
        self,
        *,
        period: str,
        report_id: str,
        recipient: str,
        render_version: str,
        now: datetime,
    ) -> bool:
        with self._sessions() as session:
            existing = session.get(DigestDeliveryRecord, period)
            if existing is not None:
                if existing.report_id != report_id or existing.recipient != recipient:
                    raise ValueError(
                        "delivery period is already bound to another report"
                    )
                return False
            session.add(
                DigestDeliveryRecord(
                    period=period,
                    report_id=report_id,
                    recipient=recipient,
                    render_version=render_version,
                    state=DeliveryState.PENDING.value,
                    next_attempt_at=now,
                    attempt_count=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            return True

    def claim(
        self,
        *,
        now: datetime,
        stale_after: timedelta = timedelta(minutes=15),
    ) -> DeliveryClaim | None:
        with self._sessions() as session:
            record = session.scalar(
                select(DigestDeliveryRecord)
                .where(
                    or_(
                        (
                            DigestDeliveryRecord.state.in_(
                                (
                                    DeliveryState.PENDING.value,
                                    DeliveryState.RETRYABLE_FAILURE.value,
                                )
                            )
                            & (DigestDeliveryRecord.next_attempt_at <= now)
                        ),
                        (
                            (DigestDeliveryRecord.state == DeliveryState.SENDING.value)
                            & (DigestDeliveryRecord.claimed_at <= now - stale_after)
                        ),
                    )
                )
                .order_by(DigestDeliveryRecord.next_attempt_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if record is None:
                return None
            token = uuid4()
            record.state = DeliveryState.SENDING.value
            record.claim_token = token
            record.claimed_at = now
            record.attempt_count += 1
            record.updated_at = now
            session.commit()
            return DeliveryClaim(
                record.period,
                record.report_id,
                record.recipient,
                record.render_version,
                token,
                record.attempt_count,
            )

    def acknowledge(
        self, claim: DeliveryClaim, acknowledgement: MailAcknowledgement
    ) -> None:
        with self._sessions() as session:
            record = self._claimed(session, claim)
            record.state = DeliveryState.SENT.value
            record.provider_message_id = acknowledgement.provider_message_id
            record.acknowledged_at = acknowledgement.acknowledged_at
            record.sent_at = acknowledgement.acknowledged_at
            record.claim_token = None
            record.claimed_at = None
            record.last_error = None
            record.updated_at = acknowledgement.acknowledged_at
            session.commit()

    def fail(
        self,
        claim: DeliveryClaim,
        *,
        now: datetime,
        retry_at: datetime,
        reason: str = "mail-transport-failed",
    ) -> None:
        with self._sessions() as session:
            record = self._claimed(session, claim)
            record.state = DeliveryState.RETRYABLE_FAILURE.value
            record.next_attempt_at = retry_at
            record.claim_token = None
            record.claimed_at = None
            record.last_error = reason[:500]
            record.updated_at = now
            session.commit()

    @staticmethod
    def _claimed(session: Session, claim: DeliveryClaim) -> DigestDeliveryRecord:
        record = session.scalar(
            select(DigestDeliveryRecord)
            .where(
                DigestDeliveryRecord.period == claim.period,
                DigestDeliveryRecord.state == DeliveryState.SENDING.value,
                DigestDeliveryRecord.claim_token == claim.claim_token,
            )
            .with_for_update()
        )
        if record is None:
            raise RuntimeError("delivery claim is no longer valid")
        return record


class FridayScheduler:
    @staticmethod
    def scheduled_at(period: str) -> datetime:
        try:
            year = int(period[:4])
            week = int(period[6:])
            friday = date.fromisocalendar(year, week, 5)
        except (ValueError, IndexError) as error:
            raise ValueError("period must use ISO YYYY-Www format") from error
        return datetime.combine(friday, time(10, 0), tzinfo=WARSAW)

    @classmethod
    def most_recent_due_period(cls, now: datetime) -> str:
        local = now.astimezone(WARSAW)
        monday = local.date() - timedelta(days=local.weekday())
        friday = monday + timedelta(days=4)
        scheduled = datetime.combine(friday, time(10, 0), tzinfo=WARSAW)
        if local < scheduled:
            friday -= timedelta(days=7)
        iso = friday.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}"


class HttpMailTransport(MailTransport):
    """HTTPS provider adapter requiring idempotency acknowledgement."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_host: str,
        token_file: Path,
        sender: str,
        timeout_seconds: float = 10.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname != allowed_host
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("mail endpoint must match the approved HTTPS host")
        self._endpoint = endpoint
        self._token_file = token_file
        self._sender = sender
        self._timeout = timeout_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def send(
        self,
        *,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str,
        idempotency_key: str,
    ) -> MailAcknowledgement:
        token = read_secret_text(self._token_file)
        request = Request(  # noqa: S310 - validated HTTPS endpoint and host
            self._endpoint,
            data=json.dumps(
                {
                    "from": self._sender,
                    "to": [recipient],
                    "subject": subject,
                    "html": html_body,
                    "text": text_body,
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                payload = json.load(response)
            message_id = payload.get("id")
            if not isinstance(message_id, str) or not message_id:
                raise ValueError
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("mail provider did not acknowledge delivery") from error
        return MailAcknowledgement(message_id, self._clock())


class DeliveryWorker:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        outbox: DeliveryOutbox,
        transport: MailTransport,
    ) -> None:
        self._sessions = sessions
        self._outbox = outbox
        self._transport = transport

    def run_once(self, *, now: datetime) -> bool:
        claim = self._outbox.claim(now=now)
        if claim is None:
            return False
        try:
            with self._sessions() as session:
                report = session.get(ReportDraftRecord, UUID(claim.report_id))
                if report is None or report.status != "prepared":
                    raise RuntimeError("prepared report is unavailable")
                acknowledgement = self._transport.send(
                    recipient=claim.recipient,
                    subject=f"Homefinder weekly report {claim.period}",
                    html_body=report.html_body,
                    text_body=report.text_body,
                    idempotency_key=f"homez:{claim.period}:{claim.report_id}",
                )
        except Exception:
            delay = min(21_600, 60 * (2 ** max(0, claim.attempt_number - 1)))
            self._outbox.fail(
                claim,
                now=now,
                retry_at=now + timedelta(seconds=delay),
            )
        else:
            self._outbox.acknowledge(claim, acknowledgement)
        return True
