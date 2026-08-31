from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

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
        self._label_id = label_id
        self._processed_label = processed_label
        self._quarantine_label = quarantine_label

    def poll(self) -> PollResult:
        ids = self._gmail.list_messages(
            label_id=self._label_id, limit=self._policy.max_messages_per_poll
        )
        result = PollResult(len(ids), 0, 0, 0, 0)
        for message_id in ids:
            try:
                message = self._gmail.get_message(message_id)
                ingested = self._ingestion.ingest(message.raw_message)
            except AlertParseError as error:
                reason = f"{self._source_key}@{self._ingestion.parser_version}: {error}"
                self._quarantine(message, reason)
                self._gmail.modify_labels(
                    message_id,
                    add=(self._quarantine_label,),
                    remove=(self._label_id,),
                )
                result = _replace(result, quarantined=result.quarantined + 1)
            except Exception:
                self._record_error("poll failed; message remains available for retry")
                result = _replace(result, failed=result.failed + 1)
                continue
            else:
                self._gmail.modify_labels(
                    message_id,
                    add=(self._processed_label,),
                    remove=(self._label_id,),
                )
                result = _replace(
                    result,
                    ingested=result.ingested + int(ingested.created),
                    duplicates=result.duplicates + int(not ingested.created),
                )
        self._record_success()
        return result

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

    def _record_success(self) -> None:
        state = self._session.scalar(
            select(IngestionStateRecord).where(
                IngestionStateRecord.source_key == self._source_key
            )
        )
        if state is None:
            state = IngestionStateRecord(source_key=self._source_key)
            self._session.add(state)
        state.last_success_at = datetime.now(timezone.utc)
        state.last_error = None
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
        self._session.commit()


def _replace(result: PollResult, **changes: int) -> PollResult:
    values = {field: getattr(result, field) for field in result.__dataclass_fields__}
    values.update(changes)
    return PollResult(**values)
