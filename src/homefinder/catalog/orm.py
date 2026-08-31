from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SourceRecord(Base):
    __tablename__ = "sources"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))


class ListingRecord(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source_id", "source_listing_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    source_listing_id: Mapped[str] = mapped_column(String(255))
    canonical_url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(500))


class ListingSnapshotRecord(Base):
    __tablename__ = "listing_snapshots"
    __table_args__ = (UniqueConstraint("listing_id", "content_hash"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    listing_id: Mapped[UUID] = mapped_column(ForeignKey("listings.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price_minor: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    area_sqm: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    rooms: Mapped[int]
    availability: Mapped[str] = mapped_column(String(20))
    location: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))


class PropertyCandidateRecord(Base):
    __tablename__ = "property_candidates"

    id: Mapped[UUID] = mapped_column(primary_key=True)


class CandidateListingRecord(Base):
    __tablename__ = "candidate_listings"

    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("property_candidates.id"), primary_key=True
    )
    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("listings.id"), primary_key=True
    )


class SourceMessageRecord(Base):
    __tablename__ = "source_messages"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    provider_message_id: Mapped[str] = mapped_column(String(255), unique=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sender: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(500))
    raw_sha256: Mapped[str] = mapped_column(String(64))
    listing_id: Mapped[UUID] = mapped_column(ForeignKey("listings.id"))
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("listing_snapshots.id"))
    candidate_id: Mapped[UUID] = mapped_column(ForeignKey("property_candidates.id"))


class QuarantinedMessageRecord(Base):
    __tablename__ = "quarantined_messages"

    provider_message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(100), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_message: Mapped[bytes] = mapped_column(LargeBinary)
    reason: Mapped[str] = mapped_column(String(500))


class IngestionStateRecord(Base):
    __tablename__ = "ingestion_states"

    source_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
