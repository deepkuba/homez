import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from homefinder.catalog.deduplication import (
    deterministic_duplicate_key,
    duplicate_evidence,
)
from homefinder.catalog.orm import (
    CandidateListingRecord,
    CandidatePresentationRecord,
    DuplicateEvidenceRecord,
    ListingRecord,
    ListingSnapshotRecord,
    PropertyCandidateRecord,
    SourceMessageRecord,
    SourceRecord,
)
from homefinder.domain.models import Listing, ListingSnapshot, ParsedAlert


@dataclass(frozen=True, slots=True)
class CatalogWriteResult:
    listing: Listing
    snapshot: ListingSnapshot
    created: bool


class SqlAlchemyCatalogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_alert(self, alert: ParsedAlert) -> CatalogWriteResult:
        with self._session.begin():
            existing_message = self._session.scalar(
                select(SourceMessageRecord).where(
                    SourceMessageRecord.provider_message_id
                    == alert.message.provider_message_id
                )
            )
            if existing_message is not None:
                listing_record = self._session.get(
                    ListingRecord, existing_message.listing_id
                )
                snapshot_record = self._session.get(
                    ListingSnapshotRecord, existing_message.snapshot_id
                )
                if listing_record is None or snapshot_record is None:
                    raise RuntimeError("catalog message references missing records")
                return CatalogWriteResult(
                    listing=_listing_from_record(listing_record),
                    snapshot=_snapshot_from_record(snapshot_record),
                    created=False,
                )

            source = self._session.get(SourceRecord, alert.source.id)
            if source is None:
                self._session.add(
                    SourceRecord(
                        id=alert.source.id,
                        key=alert.source.key,
                        display_name=alert.source.display_name,
                    )
                )

            listing_record = self._session.scalar(
                select(ListingRecord).where(
                    ListingRecord.source_id == alert.listing.source_id,
                    ListingRecord.source_listing_id == alert.listing.source_listing_id,
                )
            )
            if listing_record is None:
                listing_record = ListingRecord(
                    id=alert.listing.id,
                    source_id=alert.listing.source_id,
                    source_listing_id=alert.listing.source_listing_id,
                    canonical_url=alert.listing.canonical_url,
                    title=alert.listing.title,
                )
                self._session.add(listing_record)
                self._session.flush()

            snapshot_record = self._session.scalar(
                select(ListingSnapshotRecord).where(
                    ListingSnapshotRecord.listing_id == listing_record.id,
                    ListingSnapshotRecord.content_hash == alert.snapshot.content_hash,
                )
            )
            if snapshot_record is None:
                snapshot_record = ListingSnapshotRecord(
                    id=alert.snapshot.id,
                    listing_id=listing_record.id,
                    observed_at=alert.snapshot.observed_at,
                    price_minor=alert.snapshot.price_minor,
                    currency=alert.snapshot.currency,
                    area_sqm=alert.snapshot.area_sqm,
                    rooms=alert.snapshot.rooms,
                    availability=alert.snapshot.availability,
                    location=alert.snapshot.location,
                    description=alert.snapshot.description,
                    content_hash=alert.snapshot.content_hash,
                )
                self._session.add(snapshot_record)
                self._session.flush()

            key = deterministic_duplicate_key(alert.listing, alert.snapshot)
            candidate = self._session.scalar(
                select(PropertyCandidateRecord).where(
                    PropertyCandidateRecord.deterministic_key == key
                )
            )
            if candidate is None:
                candidate = PropertyCandidateRecord(id=uuid4(), deterministic_key=key)
                self._session.add(candidate)
                self._session.flush()
            candidate_id = candidate.id
            linked = self._session.scalar(
                select(CandidateListingRecord).where(
                    CandidateListingRecord.candidate_id == candidate_id,
                    CandidateListingRecord.listing_id == listing_record.id,
                )
            )
            if linked is None:
                self._session.add(
                    CandidateListingRecord(
                        candidate_id=candidate_id, listing_id=listing_record.id
                    )
                )
            self._record_fuzzy_evidence(listing_record, snapshot_record)
            self._session.add(
                SourceMessageRecord(
                    id=alert.message.id,
                    source_id=alert.message.source_id,
                    provider_message_id=alert.message.provider_message_id,
                    received_at=alert.message.received_at,
                    sender=alert.message.sender,
                    subject=alert.message.subject,
                    raw_sha256=alert.message.raw_sha256,
                    listing_id=listing_record.id,
                    snapshot_id=snapshot_record.id,
                    candidate_id=candidate_id,
                )
            )
            result = CatalogWriteResult(
                listing=_listing_from_record(listing_record),
                snapshot=_snapshot_from_record(snapshot_record),
                created=True,
            )

        return result

    def _record_fuzzy_evidence(
        self, listing: ListingRecord, snapshot: ListingSnapshotRecord
    ) -> None:
        current_listing = _listing_from_record(listing)
        current_snapshot = _snapshot_from_record(snapshot)
        existing = self._session.scalars(
            select(ListingRecord).where(ListingRecord.id != listing.id)
        )
        for other in existing:
            other_snapshot = self._session.scalar(
                select(ListingSnapshotRecord)
                .where(ListingSnapshotRecord.listing_id == other.id)
                .order_by(ListingSnapshotRecord.observed_at.desc())
            )
            if other_snapshot is None:
                continue
            confidence, reasons = duplicate_evidence(
                current_listing,
                current_snapshot,
                _listing_from_record(other),
                _snapshot_from_record(other_snapshot),
            )
            if confidence < 0.60:
                continue
            low, high = sorted((listing.id, other.id), key=str)
            if (
                self._session.scalar(
                    select(DuplicateEvidenceRecord).where(
                        DuplicateEvidenceRecord.listing_id == low,
                        DuplicateEvidenceRecord.possible_listing_id == high,
                    )
                )
                is None
            ):
                self._session.add(
                    DuplicateEvidenceRecord(
                        id=uuid4(),
                        listing_id=low,
                        possible_listing_id=high,
                        confidence=confidence,
                        reasons=json.dumps(reasons),
                        status="pending",
                    )
                )

    def merge_candidates(self, kept_id: UUID, merged_id: UUID) -> None:
        """Explicitly merge candidates after human review."""
        if kept_id == merged_id:
            raise ValueError("cannot merge a candidate into itself")
        try:
            links = self._session.scalars(
                select(CandidateListingRecord).where(
                    CandidateListingRecord.candidate_id == merged_id
                )
            ).all()
            for link in links:
                already = self._session.get(
                    CandidateListingRecord, (kept_id, link.listing_id)
                )
                if already is None:
                    link.candidate_id = kept_id
                else:
                    self._session.delete(link)
            merged = self._session.get(PropertyCandidateRecord, merged_id)
            if merged is None:
                raise ValueError("candidate does not exist")
            self._session.delete(merged)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def split_listing(self, candidate_id: UUID, listing_id: UUID) -> None:
        """Explicitly move one advertisement into a new candidate."""
        try:
            link = self._session.get(CandidateListingRecord, (candidate_id, listing_id))
            if link is None:
                raise ValueError("listing is not linked to candidate")
            listing = self._session.get(ListingRecord, listing_id)
            if listing is None:
                raise ValueError("listing does not exist")
            new_candidate = PropertyCandidateRecord(
                id=uuid4(), deterministic_key=f"manual:{listing_id}"
            )
            self._session.add(new_candidate)
            link.candidate_id = new_candidate.id
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def record_presentation(
        self,
        candidate_id: UUID,
        snapshot_id: UUID,
        presented_at: datetime,
        dismissed: bool = False,
    ) -> None:
        try:
            self._session.add(
                CandidatePresentationRecord(
                    id=uuid4(),
                    candidate_id=candidate_id,
                    snapshot_id=snapshot_id,
                    presented_at=presented_at,
                    dismissed=dismissed,
                )
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def should_resurface(
        self,
        candidate_id: UUID,
        current_snapshot_id: UUID,
        now: datetime,
        cooldown: timedelta,
    ) -> bool:
        presentation = self._session.scalar(
            select(CandidatePresentationRecord)
            .where(CandidatePresentationRecord.candidate_id == candidate_id)
            .order_by(CandidatePresentationRecord.presented_at.desc())
        )
        if presentation is None:
            return True
        if current_snapshot_id != presentation.snapshot_id:
            return True
        presented_at = presentation.presented_at
        if presented_at.tzinfo is None:
            presented_at = presented_at.replace(tzinfo=timezone.utc)
        return now - presented_at >= cooldown


def _listing_from_record(record: ListingRecord) -> Listing:
    return Listing(
        id=record.id,
        source_id=record.source_id,
        source_listing_id=record.source_listing_id,
        canonical_url=record.canonical_url,
        title=record.title,
    )


def _snapshot_from_record(record: ListingSnapshotRecord) -> ListingSnapshot:
    return ListingSnapshot(
        id=record.id,
        listing_id=record.listing_id,
        observed_at=record.observed_at,
        price_minor=record.price_minor,
        currency=record.currency,
        area_sqm=record.area_sqm,
        rooms=record.rooms,
        availability=record.availability,
        location=record.location,
        description=record.description,
        content_hash=record.content_hash,
    )
