from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy as email_policy
from email.parser import BytesParser
from email.utils import parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from homefinder.application.gmail_labels import GmailLabelIds
from homefinder.application.ingest_alert import AlertIngestionService
from homefinder.catalog.orm import IngestionStateRecord, QuarantinedMessageRecord
from homefinder.sources.errors import AlertParseError
from homefinder.sources.gmail import GmailClient, GmailMessage
from homefinder.sources.policy import SourcePolicyRegistry


@dataclass(frozen=True, slots=True)
class PollResult:
    fetched: int
    ingested: int
    duplicates: int
    quarantined: int
    failed: int


class GmailPollingService:
    def __init__(
        self,
        session: Session,
        gmail: GmailClient,
        ingestion: AlertIngestionService,
        policies: SourcePolicyRegistry,
        source_key: str,
        mailbox_key: str = "default",
        labels: GmailLabelIds | None = None,
        label_id: str = "INBOX",
        processed_label: str = "HOMEZ_PROCESSED",
        quarantine_label: str = "HOMEZ_QUARANTINE",
    ) -> None:
        self._session = session
        self._gmail = gmail
        self._ingestion = ingestion
        self._policy = policies.require(source_key)
        if ingestion.source_key != source_key:
            raise ValueError("source policy and parser must have the same key")
        self._source_key = source_key
        self._mailbox_key = mailbox_key
        self._strict_labels = labels is not None
        self._labels = labels or GmailLabelIds(
            alert=label_id,
            processed=processed_label,
            quarantine=quarantine_label,
            retry="HOMEZ_RETRY",
        )
        expected_senders = frozenset(
            sender.casefold() for sender in self._policy.allowed_senders
        )
        if (
            expected_senders != frozenset({ingestion.expected_sender.casefold()})
            or frozenset(host.casefold() for host in self._policy.allowed_hosts)
            != frozenset({ingestion.expected_host.casefold()})
            or self._policy.max_message_bytes != ingestion.max_message_bytes
        ):
            raise ValueError("source policy and parser contract must match")

    def poll(self) -> PollResult:
        ids = self._gmail.list_messages(
            label_id=self._labels.alert, limit=self._policy.max_messages_per_poll
        )
        result = PollResult(len(ids), 0, 0, 0, 0)
        for message_id in ids:
            try:
                message = self._gmail.get_message(message_id)
                if self._strict_labels and self._labels.alert not in message.label_ids:
                    self._record_error("gmail-message-processing-failed")
                    result = _replace(result, failed=result.failed + 1)
                    continue
                policy_failure = self._policy_failure(message.raw_message)
                if policy_failure is not None:
                    self._quarantine(message, policy_failure)
                    result = _replace(result, quarantined=result.quarantined + 1)
                    continue
                ingested = self._ingestion.ingest(message.raw_message)
            except AlertParseError as error:
                reason = f"{self._source_key}@{self._ingestion.parser_version}: {error}"
                self._quarantine(message, reason)
                self._gmail.modify_labels(
                    message_id,
                    add=(self._labels.quarantine,),
                    remove=(
                        self._labels.alert,
                        self._labels.retry,
                        self._labels.processed,
                    )
                    if self._strict_labels
                    else (self._labels.alert,),
                )
                result = _replace(result, quarantined=result.quarantined + 1)
            except Exception:
                self._record_error("gmail-message-processing-failed")
                self._gmail.modify_labels(
                    message_id,
                    add=(self._labels.retry,),
                    remove=(self._labels.processed, self._labels.quarantine),
                )
                result = _replace(result, failed=result.failed + 1)
                continue
            else:
                self._gmail.modify_labels(
                    message_id,
                    add=(self._labels.processed,),
                    remove=(
                        self._labels.alert,
                        self._labels.retry,
                        self._labels.quarantine,
                    )
                    if self._strict_labels
                    else (self._labels.alert,),
                )
                result = _replace(
                    result,
                    ingested=result.ingested + int(ingested.created),
                    duplicates=result.duplicates + int(not ingested.created),
                )
        if result.failed == 0:
            self._record_success(quarantined=result.quarantined)
        return result

    def _policy_failure(self, raw_message: bytes) -> str | None:
        if not raw_message or len(raw_message) > self._policy.max_message_bytes:
            return "message-size"
        message = BytesParser(policy=email_policy.default).parsebytes(
            raw_message, headersonly=True
        )
        addresses = message.get_all("From", [])
        if len(addresses) != 1:
            return "unexpected-sender"
        sender = parseaddr(addresses[0])[1].casefold()
        if not sender or not self._policy.allows_sender(sender):
            return "unexpected-sender"
        return None

    def _quarantine(self, message: GmailMessage, reason: str) -> None:
        if self._session.get(QuarantinedMessageRecord, message.provider_message_id):
            return
        record = QuarantinedMessageRecord(
            provider_message_id=message.provider_message_id,
            source_key=self._source_key,
            received_at=datetime.now(timezone.utc),
            raw_message=message.raw_message,
            reason=reason,
            parser_version=self._ingestion.parser_version,
        )
        self._session.add(record)
        self._session.commit()

    def _record_success(self, *, quarantined: int) -> None:
        state = self._session.scalar(
            select(IngestionStateRecord).where(
                IngestionStateRecord.source_key == self._source_key
            )
        )
        if state is None:
            state = IngestionStateRecord(source_key=self._source_key)
            self._session.add(state)
        state.last_success_at = datetime.now(timezone.utc)
        state.last_poll_at = state.last_success_at
        state.last_error = None
        state.status = "healthy" if quarantined == 0 else "degraded"
        state.consecutive_failures = 0
        if quarantined:
            state.last_quarantine_at = state.last_success_at
            state.quarantine_count = (state.quarantine_count or 0) + quarantined
        self._session.commit()

    def _record_error(self, reason: str) -> None:
        state = self._session.scalar(
            select(IngestionStateRecord).where(
                IngestionStateRecord.source_key == self._source_key
            )
        )
        if state is None:
            state = IngestionStateRecord(source_key=self._source_key)
            self._session.add(state)
        state.last_error = reason
        state.last_poll_at = datetime.now(timezone.utc)
        state.last_error_at = state.last_poll_at
        state.status = "degraded"
        state.consecutive_failures = (state.consecutive_failures or 0) + 1
        self._session.commit()


def _replace(result: PollResult, **changes: int) -> PollResult:
    values = {field: getattr(result, field) for field in result.__dataclass_fields__}
    values.update(changes)
    return PollResult(**values)
