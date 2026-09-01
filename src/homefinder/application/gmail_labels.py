"""Resolve Gmail label names to durable mailbox-scoped IDs."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from homefinder.catalog.orm import GmailLabelBindingRecord
from homefinder.sources.gmail import GmailClient, GmailLabel


@dataclass(frozen=True, slots=True)
class GmailLabelIds:
    alert: str
    processed: str
    quarantine: str
    retry: str


class GmailLabelManager:
    def __init__(self, session: Session, gmail: GmailClient) -> None:
        self._session = session
        self._gmail = gmail

    def resolve(self, *, mailbox_key: str, source_key: str) -> GmailLabelIds:
        names = {
            "alert": f"HOMEZ/{source_key}/ALERT",
            "processed": f"HOMEZ/{source_key}/PROCESSED",
            "quarantine": f"HOMEZ/{source_key}/QUARANTINE",
            "retry": f"HOMEZ/{source_key}/RETRY",
        }
        live = self._live_by_name(self._gmail.list_labels())
        resolved: dict[str, str] = {}
        now = datetime.now(timezone.utc)
        for role, name in names.items():
            record = self._session.get(
                GmailLabelBindingRecord, (mailbox_key, source_key, role)
            )
            label = live.get(name)
            if label is None:
                label = self._gmail.create_label(name)
                if label.name != name or not label.id:
                    raise ValueError("Gmail returned an invalid label mapping")
                live[name] = label
            if record is None:
                record = GmailLabelBindingRecord(
                    mailbox_key=mailbox_key,
                    source_key=source_key,
                    role=role,
                    label_name=name,
                    label_id=label.id,
                    updated_at=now,
                )
                self._session.add(record)
            elif record.label_name != name or record.label_id != label.id:
                record.label_name = name
                record.label_id = label.id
                record.updated_at = now
            resolved[role] = label.id
        self._session.commit()
        return GmailLabelIds(**resolved)

    @staticmethod
    def _live_by_name(labels: list[GmailLabel]) -> dict[str, GmailLabel]:
        result: dict[str, GmailLabel] = {}
        for label in labels:
            if label.name in result:
                raise ValueError("Gmail contains ambiguous duplicate label names")
            result[label.name] = label
        return result
