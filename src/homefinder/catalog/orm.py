from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BuyerProfileRecord(Base):
    __tablename__ = "buyer_profiles"
    __table_args__ = (
        CheckConstraint(
            "(approved_at IS NULL AND approved_by IS NULL) OR "
            "(approved_at IS NOT NULL AND approved_by IS NOT NULL)",
            name="ck_buyer_profiles_approval_complete",
        ),
    )

    version: Mapped[int] = mapped_column(primary_key=True)
    effective_from: Mapped[str] = mapped_column(String(10))
    profile_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


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
    __table_args__ = (UniqueConstraint("deterministic_key"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    deterministic_key: Mapped[str] = mapped_column(String(500))


class CandidateListingRecord(Base):
    __tablename__ = "candidate_listings"

    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("property_candidates.id"), primary_key=True
    )
    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("listings.id"), primary_key=True
    )


class DuplicateEvidenceRecord(Base):
    __tablename__ = "duplicate_evidence"
    __table_args__ = (
        UniqueConstraint("listing_id", "possible_listing_id"),
        CheckConstraint(
            "listing_id <> possible_listing_id", name="duplicate_evidence_check"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    listing_id: Mapped[UUID] = mapped_column(ForeignKey("listings.id"), index=True)
    possible_listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("listings.id"), index=True
    )
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    reasons: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")


class CandidatePresentationRecord(Base):
    __tablename__ = "candidate_presentations"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("property_candidates.id"), index=True
    )
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("listing_snapshots.id"))
    presented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dismissed: Mapped[bool] = mapped_column(default=False)


class SourceMessageRecord(Base):
    __tablename__ = "source_messages"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    provider_message_id: Mapped[str] = mapped_column(String(255), unique=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sender: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(500))
    raw_sha256: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(100), default="legacy-v1")
    listing_id: Mapped[UUID] = mapped_column(ForeignKey("listings.id"))
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("listing_snapshots.id"))
    candidate_id: Mapped[UUID] = mapped_column(ForeignKey("property_candidates.id"))


class SourceMessageItemRecord(Base):
    __tablename__ = "source_message_items"
    __table_args__ = (UniqueConstraint("message_id", "position"),)

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_messages.id"), primary_key=True
    )
    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("listings.id"), primary_key=True
    )
    position: Mapped[int]
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("listing_snapshots.id"))
    candidate_id: Mapped[UUID] = mapped_column(ForeignKey("property_candidates.id"))


class QuarantinedMessageRecord(Base):
    __tablename__ = "quarantined_messages"

    provider_message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(100), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_message: Mapped[bytes] = mapped_column(LargeBinary)
    reason: Mapped[str] = mapped_column(String(500))
    parser_version: Mapped[str] = mapped_column(String(100), default="unknown")


class IngestionStateRecord(Base):
    __tablename__ = "ingestion_states"

    source_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class RoutingQuotaLedgerRecord(Base):
    __tablename__ = "routing_quota_ledger"

    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    provider: Mapped[str] = mapped_column(String(100), primary_key=True)
    billable_unit: Mapped[str] = mapped_column(String(50), primary_key=True)
    allowance: Mapped[int]
    reserved_units: Mapped[int] = mapped_column(default=0)
    provider_blocked: Mapped[bool] = mapped_column(default=False)
    last_alert_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RouteObservationRecord(Base):
    __tablename__ = "route_observations"

    cache_key: Mapped[str] = mapped_column(String(500), primary_key=True)
    goal_version: Mapped[int]
    direction: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20))
    duration_minutes: Mapped[int]
    provider: Mapped[str] = mapped_column(String(100))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))


class DigestDeliveryRecord(Base):
    __tablename__ = "digest_deliveries"

    period: Mapped[str] = mapped_column(String(8), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(100), unique=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recipient: Mapped[str] = mapped_column(String(320))


class FeedbackTokenRecord(Base):
    __tablename__ = "feedback_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(100), index=True)
    listing_id: Mapped[str] = mapped_column(String(100), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FeedbackEventRecord(Base):
    __tablename__ = "feedback_events"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(ForeignKey("feedback_tokens.token_hash"))
    report_id: Mapped[str] = mapped_column(String(100))
    listing_id: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(20))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EnvironmentalEvidenceRecord(Base):
    __tablename__ = "environmental_evidence"
    __table_args__ = (UniqueConstraint("property_id", "field", "observed_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(String(100), index=True)
    field: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(255))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))


class EnvironmentalCorrectionRecord(Base):
    __tablename__ = "environmental_corrections"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(String(100), index=True)
    field: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(Text)
    corrected_by: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    corrected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RenovationScopeRecord(Base):
    __tablename__ = "renovation_scope_items"
    __table_args__ = (UniqueConstraint("property_id", "name"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    low_minor: Mapped[int]
    base_minor: Mapped[int]
    high_minor: Mapped[int]
    required: Mapped[bool] = mapped_column(default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RenovationComparableRecord(Base):
    __tablename__ = "renovation_comparables"
    __table_args__ = (UniqueConstraint("property_id", "comparable_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(String(100), index=True)
    comparable_id: Mapped[str] = mapped_column(String(100))
    effective_move_in_minor: Mapped[int]
    similarity: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    evidence_source: Mapped[str] = mapped_column(String(255))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    selected: Mapped[bool] = mapped_column(default=False)


class RenovationAttachmentRecord(Base):
    __tablename__ = "renovation_attachments"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    property_id: Mapped[str] = mapped_column(String(100), index=True)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(50))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PrimaryMarketProjectRecord(Base):
    __tablename__ = "primary_market_projects"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    normal_eligibility: Mapped[str] = mapped_column(String(20))
    overall_concern: Mapped[str] = mapped_column(String(30))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PrimaryMarketEntityRecord(Base):
    __tablename__ = "primary_market_entities"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(40))
    registration_reference: Mapped[str | None] = mapped_column(String(255))


class PrimaryMarketEvidenceRecord(Base):
    __tablename__ = "primary_market_evidence"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(100), index=True)
    subject_id: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(255))
    reference: Mapped[str] = mapped_column(String(500))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    permitted: Mapped[bool]
    summary: Mapped[str] = mapped_column(Text, default="")


class PrimaryMarketRiskRecord(Base):
    __tablename__ = "primary_market_risks"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(100), index=True)
    dimension: Mapped[str] = mapped_column(String(60))
    level: Mapped[str] = mapped_column(String(30))
    facts: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PrimaryMarketManualTaskRecord(Base):
    __tablename__ = "primary_market_manual_tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "subject", "reason"),
        Index("ix_primary_market_tasks_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(100))
    subject: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
