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
    SourceMessageItemRecord,
    SourceMessageRecord,
    SourceRecord,
)
from homefinder.domain.models import (
    Listing,
    ListingSnapshot,
    ParsedAlert,
    ParsedListing,
)


@dataclass(frozen=True, slots=True)
class CatalogWriteItem:
    listing: Listing
    snapshot: ListingSnapshot


@dataclass(frozen=True, slots=True)
class CatalogWriteResult:
    items: tuple[CatalogWriteItem, ...]
    created: bool

    @property
    def listing(self) -> Listing:
        return self.items[0].listing

    @property
    def snapshot(self) -> ListingSnapshot:
        return self.items[0].snapshot


class ImmutableMessageConflictError(ValueError):
    """The provider reused an immutable message ID for different content."""


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
                if (
                    existing_message.source_id != alert.message.source_id
                    or existing_message.raw_sha256 != alert.message.raw_sha256
                ):
                    raise ImmutableMessageConflictError
                return CatalogWriteResult(
                    items=self._load_message_items(existing_message),
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

            stored = tuple(self._store_item(item) for item in alert.items)
            first_listing, first_snapshot, first_candidate_id = stored[0]
            message_record = SourceMessageRecord(
                id=alert.message.id,
                source_id=alert.message.source_id,
                provider_message_id=alert.message.provider_message_id,
                received_at=alert.message.received_at,
                sender=alert.message.sender,
                subject=alert.message.subject,
                raw_sha256=alert.message.raw_sha256,
                parser_version=alert.message.parser_version,
                listing_id=first_listing.id,
                snapshot_id=first_snapshot.id,
                candidate_id=first_candidate_id,
            )
            self._session.add(message_record)
            self._session.flush()
            for position, (listing, snapshot, candidate_id) in enumerate(stored):
                self._session.add(
                    SourceMessageItemRecord(
                        message_id=message_record.id,
                        listing_id=listing.id,
                        position=position,
                        snapshot_id=snapshot.id,
                        candidate_id=candidate_id,
                    )
                )
            result = CatalogWriteResult(
                items=tuple(
                    CatalogWriteItem(
                        listing=_listing_from_record(listing),
                        snapshot=_snapshot_from_record(snapshot),
                    )
                    for listing, snapshot, _candidate_id in stored
                ),
                created=True,
            )

        return result

    def _load_message_items(
        self, message: SourceMessageRecord
    ) -> tuple[CatalogWriteItem, ...]:
        links = self._session.scalars(
            select(SourceMessageItemRecord)
            .where(SourceMessageItemRecord.message_id == message.id)
            .order_by(SourceMessageItemRecord.position)
        ).all()
        record_ids = (
            [(link.listing_id, link.snapshot_id) for link in links]
            if links
            else [(message.listing_id, message.snapshot_id)]
        )
        items: list[CatalogWriteItem] = []
        for listing_id, snapshot_id in record_ids:
            listing = self._session.get(ListingRecord, listing_id)
            snapshot = self._session.get(ListingSnapshotRecord, snapshot_id)
            if listing is None or snapshot is None:
                raise RuntimeError("catalog message references missing records")
            items.append(
                CatalogWriteItem(
                    listing=_listing_from_record(listing),
                    snapshot=_snapshot_from_record(snapshot),
                )
            )
        return tuple(items)

    def _store_item(
        self, item: ParsedListing
    ) -> tuple[ListingRecord, ListingSnapshotRecord, UUID]:
        listing_record = self._session.scalar(
            select(ListingRecord).where(
                ListingRecord.source_id == item.listing.source_id,
                ListingRecord.source_listing_id == item.listing.source_listing_id,
            )
        )
        if listing_record is None:
            listing_record = ListingRecord(
                id=item.listing.id,
                source_id=item.listing.source_id,
                source_listing_id=item.listing.source_listing_id,
                canonical_url=item.listing.canonical_url,
                title=item.listing.title,
            )
            self._session.add(listing_record)
            self._session.flush()

        snapshot_record = self._session.scalar(
            select(ListingSnapshotRecord).where(
                ListingSnapshotRecord.listing_id == listing_record.id,
                ListingSnapshotRecord.content_hash == item.snapshot.content_hash,
            )
        )
        if snapshot_record is None:
            snapshot_record = ListingSnapshotRecord(
                id=item.snapshot.id,
                listing_id=listing_record.id,
                observed_at=item.snapshot.observed_at,
                price_minor=item.snapshot.price_minor,
                currency=item.snapshot.currency,
                area_sqm=item.snapshot.area_sqm,
                rooms=item.snapshot.rooms,
                availability=item.snapshot.availability,
                location=item.snapshot.location,
                description=item.snapshot.description,
                content_hash=item.snapshot.content_hash,
            )
            self._session.add(snapshot_record)
            self._session.flush()

        key = deterministic_duplicate_key(
            _listing_from_record(listing_record), _snapshot_from_record(snapshot_record)
        )
        candidate = self._session.scalar(
            select(PropertyCandidateRecord).where(
                PropertyCandidateRecord.deterministic_key == key
            )
        )
        if candidate is None:
            candidate = PropertyCandidateRecord(id=uuid4(), deterministic_key=key)
            self._session.add(candidate)
            self._session.flush()
        linked = self._session.scalar(
            select(CandidateListingRecord).where(
                CandidateListingRecord.candidate_id == candidate.id,
                CandidateListingRecord.listing_id == listing_record.id,
            )
        )
        if linked is None:
            self._session.add(
                CandidateListingRecord(
                    candidate_id=candidate.id, listing_id=listing_record.id
                )
            )
        self._record_fuzzy_evidence(listing_record, snapshot_record)
        return listing_record, snapshot_record, candidate.id

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
