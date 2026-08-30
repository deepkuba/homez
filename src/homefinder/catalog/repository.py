from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from homefinder.catalog.orm import (
    CandidateListingRecord,
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

            candidate_id = uuid4()
            self._session.add(PropertyCandidateRecord(id=candidate_id))
            self._session.add(
                CandidateListingRecord(
                    candidate_id=candidate_id,
                    listing_id=listing_record.id,
                )
            )
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
