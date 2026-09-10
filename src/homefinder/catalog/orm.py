from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
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
    __table_args__ = (
        UniqueConstraint("source_id", "source_listing_id"),
        CheckConstraint(
            "lifecycle_state IN ('active', 'stale', 'inactive')",
            name="ck_listing_lifecycle",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    source_listing_id: Mapped[str] = mapped_column(String(255))
    canonical_url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(500))
    lifecycle_state: Mapped[str] = mapped_column(String(12), server_default="active")
    lifecycle_evidence: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ListingSnapshotRecord(Base):
    __tablename__ = "listing_snapshots"
    __table_args__ = (UniqueConstraint("listing_id", "content_hash"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    listing_id: Mapped[UUID] = mapped_column(ForeignKey("listings.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price_minor: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    rooms: Mapped[int | None] = mapped_column(nullable=True)
    availability: Mapped[str] = mapped_column(String(20))
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
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
    report_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    section: Mapped[str | None] = mapped_column(String(20), nullable=True)
    material_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    last_poll_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_quarantine_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    quarantine_count: Mapped[int] = mapped_column(default=0)


class GmailLabelBindingRecord(Base):
    __tablename__ = "gmail_label_bindings"
    __table_args__ = (UniqueConstraint("mailbox_key", "source_key", "label_name"),)

    mailbox_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    label_name: Mapped[str] = mapped_column(String(225))
    label_id: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RoutingQuotaLedgerRecord(Base):
    __tablename__ = "routing_quota_ledger"

    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    provider: Mapped[str] = mapped_column(String(100), primary_key=True)
    billable_unit: Mapped[str] = mapped_column(String(50), primary_key=True)
    allowance: Mapped[int]
    safety_ceiling: Mapped[int]
    reserved_units: Mapped[int] = mapped_column(default=0)
    provider_blocked: Mapped[bool] = mapped_column(default=False)
    last_alert_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RouteObservationRecord(Base):
    __tablename__ = "route_observations"

    cache_key: Mapped[str] = mapped_column(String(500), primary_key=True)
    origin: Mapped[str] = mapped_column(String(1000))
    destination: Mapped[str] = mapped_column(String(1000))
    goal_version: Mapped[int]
    direction: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    time_semantics: Mapped[str] = mapped_column(String(20))
    duration_minutes: Mapped[int]
    provider: Mapped[str] = mapped_column(String(100))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    advisories: Mapped[str] = mapped_column(Text, default="[]")


class PendingRouteQueryRecord(Base):
    __tablename__ = "pending_route_queries"

    cache_key: Mapped[str] = mapped_column(String(500), primary_key=True)
    provider: Mapped[str] = mapped_column(String(100), index=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str] = mapped_column(String(200))


class WorkflowJobRecord(Base):
    __tablename__ = "workflow_jobs"
    __table_args__ = (
        Index("ix_workflow_jobs_claim", "state", "available_at", "priority"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(500), unique=True)
    payload_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), index=True)
    priority: Mapped[int] = mapped_column(default=100)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=8)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(nullable=True, unique=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    parent_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workflow_jobs.id"), nullable=True
    )
    root_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workflow_jobs.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)


class WorkflowJobAttemptRecord(Base):
    __tablename__ = "workflow_job_attempts"

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_jobs.id"), primary_key=True
    )
    attempt_number: Mapped[int] = mapped_column(primary_key=True)
    lease_token: Mapped[UUID] = mapped_column(unique=True)
    worker_id: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)


class CandidateFactSetRecord(Base):
    __tablename__ = "candidate_fact_sets"
    __table_args__ = (
        UniqueConstraint("candidate_id", "snapshot_id", "normalizer_version"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("property_candidates.id"), index=True
    )
    listing_id: Mapped[UUID] = mapped_column(ForeignKey("listings.id"))
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("listing_snapshots.id"))
    normalizer_version: Mapped[str] = mapped_column(String(50))
    facts_schema_version: Mapped[int]
    facts_json: Mapped[str] = mapped_column(Text)
    facts_hash: Mapped[str] = mapped_column(String(64))
    material_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CandidateMatchEvaluationRecord(Base):
    __tablename__ = "candidate_match_evaluations"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("property_candidates.id"), index=True
    )
    listing_id: Mapped[UUID] = mapped_column(ForeignKey("listings.id"))
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("listing_snapshots.id"))
    fact_set_id: Mapped[UUID] = mapped_column(ForeignKey("candidate_fact_sets.id"))
    buyer_profile_version: Mapped[int] = mapped_column(
        ForeignKey("buyer_profiles.version")
    )
    routing_goal_version: Mapped[int]
    matcher_version: Mapped[str] = mapped_column(String(50))
    input_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    facts_json: Mapped[str] = mapped_column(Text)
    explanation_json: Mapped[str] = mapped_column(Text)
    eligible: Mapped[bool]
    contains_unknown_hard_rule: Mapped[bool]
    score: Mapped[Decimal] = mapped_column(Numeric(8, 3))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReportDraftRecord(Base):
    __tablename__ = "report_drafts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    report_key: Mapped[str] = mapped_column(String(64), unique=True)
    period: Mapped[str] = mapped_column(String(10), index=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    buyer_profile_version: Mapped[int] = mapped_column(
        ForeignKey("buyer_profiles.version")
    )
    routing_goal_version: Mapped[int]
    selection_version: Mapped[str] = mapped_column(String(50))
    render_version: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    html_body: Mapped[str] = mapped_column(Text)
    text_body: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReportItemRecord(Base):
    __tablename__ = "report_items"
    __table_args__ = (UniqueConstraint("report_id", "candidate_id"),)

    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_drafts.id"), primary_key=True
    )
    section: Mapped[str] = mapped_column(String(20), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(ForeignKey("property_candidates.id"))
    listing_id: Mapped[UUID] = mapped_column(ForeignKey("listings.id"))
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("listing_snapshots.id"))
    evaluation_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_match_evaluations.id")
    )
    canonical_url: Mapped[str] = mapped_column(String(2048))
    material_fingerprint: Mapped[str] = mapped_column(String(64))
    selection_reason: Mapped[str] = mapped_column(Text)


class DigestDeliveryRecord(Base):
    __tablename__ = "digest_deliveries"

    period: Mapped[str] = mapped_column(String(10), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(100), unique=True)
    recipient: Mapped[str] = mapped_column(String(320))
    render_version: Mapped[str] = mapped_column(String(50), default="legacy")
    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    attempt_count: Mapped[int] = mapped_column(default=0)
    claim_token: Mapped[UUID | None] = mapped_column(nullable=True, unique=True)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_message_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeedbackTokenRecord(Base):
    __tablename__ = "feedback_tokens"
    __table_args__ = (UniqueConstraint("report_id", "listing_id", "scope"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(100), index=True)
    listing_id: Mapped[str] = mapped_column(String(100), index=True)
    scope: Mapped[str] = mapped_column(String(30), default="feedback")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FeedbackEventRecord(Base):
    __tablename__ = "feedback_events"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    token_hash: Mapped[str | None] = mapped_column(
        ForeignKey("feedback_tokens.token_hash"), nullable=True
    )
    report_id: Mapped[str] = mapped_column(String(100))
    listing_id: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actor_hash: Mapped[str] = mapped_column(String(64), index=True)
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


class ParserReleaseRecord(Base):
    """Immutable content-addressed parser build and lifecycle state."""

    __tablename__ = "parser_releases"
    __table_args__ = (
        CheckConstraint(
            "source IN ('gratka', 'morizon', 'otodom', 'olx')",
            name="ck_parser_release_source",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'retired', 'revoked')",
            name="ck_parser_release_status",
        ),
        UniqueConstraint("source", "release_hash"),
    )

    release_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parser_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    configuration_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dependency_lock_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deployable_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    qualifying_benchmark_run: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    status: Mapped[str] = mapped_column(String(12), server_default="draft")


class PageCaptureRecord(Base):
    """Immutable download metadata, distinct from catalog observations."""

    __tablename__ = "page_captures"
    __table_args__ = (
        CheckConstraint(
            "size_bytes >= 0 AND size_bytes <= 2000000",
            name="ck_page_capture_size",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("listing_snapshots.id"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]


class ScrapeTaskRecord(Base):
    """Central production task state, accessible only through the coordinator."""

    __tablename__ = "scrape_tasks"
    __table_args__ = (
        CheckConstraint(
            "source IN ('gratka', 'morizon', 'otodom', 'olx')",
            name="ck_scrape_task_source",
        ),
        CheckConstraint(
            "task_class IN ('live', 'artifact_recovery', 'network_recovery')",
            name="ck_scrape_task_class",
        ),
        CheckConstraint("activation_epoch > 0", name="ck_scrape_task_epoch"),
        CheckConstraint(
            "state IN ('pending', 'running', 'deferred', "
            "'succeeded', 'failed', 'held', 'superseded', 'cancelled')",
            name="ck_scrape_task_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_scrape_task_attempt_count"),
        ForeignKeyConstraint(
            ["source", "release_hash"],
            ["parser_releases.source", "parser_releases.release_hash"],
            name="fk_scrape_task_source_release",
        ),
        Index("ix_scrape_tasks_claim", "source", "state", "priority", "available_at"),
        Index("ix_scrape_tasks_status", "source", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("listing_snapshots.id"))
    canonical_url: Mapped[str] = mapped_column(String(2048))
    task_class: Mapped[str] = mapped_column(String(30))
    release_hash: Mapped[str] = mapped_column(
        ForeignKey("parser_releases.release_hash")
    )
    activation_epoch: Mapped[int]
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)

    state: Mapped[str] = mapped_column(String(20), server_default="pending")
    priority: Mapped[int] = mapped_column(server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    direct_fallback_pending: Mapped[bool] = mapped_column(server_default=false())
    direct_fallback_available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class PortalParserActivationRecord(Base):
    """Empty until manually activated; no activation command in the queue slice."""

    __tablename__ = "portal_parser_activations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source", "release_hash"],
            ["parser_releases.source", "parser_releases.release_hash"],
        ),
        CheckConstraint("activation_epoch > 0", name="ck_portal_activation_epoch"),
    )

    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    release_hash: Mapped[str] = mapped_column(String(64))
    activation_epoch: Mapped[int]


class ParserActivationAuditRecord(Base):
    """Append-only manual parser pointer change evidence."""

    __tablename__ = "parser_activation_audits"
    __table_args__ = (
        CheckConstraint(
            "action IN ('activate', 'rollback')", name="ck_parser_activation_action"
        ),
        CheckConstraint("activation_epoch > 0", name="ck_parser_audit_epoch"),
        UniqueConstraint("source", "activation_epoch"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    activation_epoch: Mapped[int]
    previous_release_hash: Mapped[str | None] = mapped_column(
        ForeignKey("parser_releases.release_hash"), nullable=True
    )
    release_hash: Mapped[str] = mapped_column(
        ForeignKey("parser_releases.release_hash")
    )
    action: Mapped[str] = mapped_column(String(12))
    actor: Mapped[str] = mapped_column(String(200))
    compared_metrics: Mapped[str] = mapped_column(String(1000))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScraperWorkerRecord(Base):
    __tablename__ = "scraper_workers"
    __table_args__ = (
        CheckConstraint("deployment IN ('nas', 'vps')", name="ck_worker_deployment"),
        CheckConstraint(
            "source IN ('gratka', 'morizon', 'otodom', 'olx')",
            name="ck_worker_source",
        ),
    )

    worker_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    deployment: Mapped[str] = mapped_column(String(3))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    release_hashes_json: Mapped[str] = mapped_column(String(4500))
    healthy: Mapped[bool]


class ScrapeAttemptRecord(Base):
    """Bounded enum diagnostics only; no text, credentials, or response bodies."""

    __tablename__ = "scrape_attempts"

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("scrape_tasks.id"), primary_key=True
    )
    attempt_number: Mapped[int] = mapped_column(primary_key=True)
    lease_token: Mapped[UUID] = mapped_column(unique=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("scraper_workers.worker_id"))
    release_hash: Mapped[str] = mapped_column(String(64))
    activation_epoch: Mapped[int]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(20))
    code: Mapped[str | None] = mapped_column(String(40))
    route_class: Mapped[str] = mapped_column(String(12), server_default="unassigned")
    route_id: Mapped[str | None] = mapped_column(String(64))
    proxy_reserved_bytes: Mapped[int] = mapped_column(server_default="0")
    network_attempt_count: Mapped[int] = mapped_column(server_default="0")
    network_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    classification: Mapped[str | None] = mapped_column(String(40))
    response_bytes: Mapped[int] = mapped_column(server_default="0")


class ProductionParserResultRecord(Base):
    """Production-only queue handoff; benchmark persistence must remain separate."""

    __tablename__ = "production_parser_results"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("scrape_tasks.id"), unique=True)
    capture_id: Mapped[UUID] = mapped_column(ForeignKey("page_captures.id"))
    release_hash: Mapped[str] = mapped_column(
        ForeignKey("parser_releases.release_hash")
    )
    activation_epoch: Mapped[int]
    variant: Mapped[str] = mapped_column(String(80))
    facts_json: Mapped[str] = mapped_column(Text)
    missing_fields_json: Mapped[str] = mapped_column(String(2048))
    result_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProductionFieldCandidateRecord(Base):
    __tablename__ = "production_field_candidates"

    result_id: Mapped[UUID] = mapped_column(
        ForeignKey("production_parser_results.id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    value_json: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(80))
    locator: Mapped[str] = mapped_column(String(200))


class ProductionResolvedFieldRecord(Base):
    __tablename__ = "production_resolved_fields"
    __table_args__ = (
        CheckConstraint(
            "state IN ('value', 'unknown', 'ambiguous')",
            name="ck_production_resolved_field_state",
        ),
    )

    result_id: Mapped[UUID] = mapped_column(
        ForeignKey("production_parser_results.id"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    state: Mapped[str] = mapped_column(String(12))
    value_json: Mapped[str | None] = mapped_column(Text)
    selected_origin: Mapped[str | None] = mapped_column(String(80))


class DiagnosticRunRecord(Base):
    """Safe central metadata; encrypted object keys and bytes remain on NAS."""

    __tablename__ = "diagnostic_runs"
    __table_args__ = (
        CheckConstraint(
            "artifact_status IN ('stored', 'unavailable')",
            name="ck_diagnostic_artifact_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    result_id: Mapped[UUID] = mapped_column(
        ForeignKey("production_parser_results.id"), unique=True
    )
    artifact_id: Mapped[str | None] = mapped_column(String(36))
    artifact_status: Mapped[str] = mapped_column(String(20))
    missing_fields_json: Mapped[str] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRecoveryBindingRecord(Base):
    """Network-free immutable capture input for one production replay task."""

    __tablename__ = "artifact_recovery_bindings"

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("scrape_tasks.id"), primary_key=True
    )
    capture_id: Mapped[UUID] = mapped_column(ForeignKey("page_captures.id"))
    artifact_id: Mapped[str] = mapped_column(String(36))
    content_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NetworkRecoveryReleaseRecord(Base):
    __tablename__ = "network_recovery_releases"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    release_hash: Mapped[str] = mapped_column(String(64))
    activation_epoch: Mapped[int]
    batch: Mapped[str] = mapped_column(String(12))
    actor: Mapped[str] = mapped_column(String(200))
    eligible_count: Mapped[int]
    released_count: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NetworkRecoveryCampaignRecord(Base):
    __tablename__ = "network_recovery_campaigns"

    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    release_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_batch: Mapped[int] = mapped_column(server_default="0")
    paused_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BenchmarkManifestRecord(Base):
    """Frozen non-production corpus; never a production scrape task."""

    __tablename__ = "benchmark_manifests"
    __table_args__ = (
        CheckConstraint(
            "data_classification = 'non-production'",
            name="ck_benchmark_manifest_non_production",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    active_release_hash: Mapped[str] = mapped_column(
        ForeignKey("parser_releases.release_hash")
    )
    candidate_release_hash: Mapped[str] = mapped_column(
        ForeignKey("parser_releases.release_hash")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    selection_policy: Mapped[str] = mapped_column(String(80))
    entries_json: Mapped[str] = mapped_column(Text)
    strata_json: Mapped[str] = mapped_column(Text)
    data_classification: Mapped[str] = mapped_column(
        String(20), server_default="non-production"
    )


class BenchmarkRunRecord(Base):
    __tablename__ = "benchmark_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'running', 'complete', 'failed')",
            name="ck_benchmark_run_state",
        ),
        CheckConstraint(
            "data_classification = 'non-production'",
            name="ck_benchmark_run_non_production",
        ),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    manifest_id: Mapped[str] = mapped_column(ForeignKey("benchmark_manifests.id"))
    source: Mapped[str] = mapped_column(String(20))
    candidate_release_hash: Mapped[str] = mapped_column(
        ForeignKey("parser_releases.release_hash")
    )
    state: Mapped[str] = mapped_column(String(12))
    processed_count: Mapped[int] = mapped_column(server_default="0")
    total_count: Mapped[int]
    unavailable_count: Mapped[int] = mapped_column(server_default="0")
    eligible: Mapped[bool] = mapped_column(server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_classification: Mapped[str] = mapped_column(
        String(20), server_default="non-production"
    )


class BenchmarkResultRecord(Base):
    """Detailed non-production output, physically separate from production results."""

    __tablename__ = "benchmark_results"
    __table_args__ = (
        CheckConstraint(
            "status IN ('compared', 'benchmark-input-unavailable')",
            name="ck_benchmark_result_status",
        ),
        CheckConstraint(
            "data_classification = 'non-production'",
            name="ck_benchmark_result_non_production",
        ),
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_runs.id"), primary_key=True
    )
    entry_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    input_kind: Mapped[str] = mapped_column(String(12))
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    variant: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30))
    active_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_classification: Mapped[str] = mapped_column(
        String(20), server_default="non-production"
    )


class BenchmarkFieldCandidateRecord(Base):
    __tablename__ = "benchmark_field_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "entry_id"],
            ["benchmark_results.run_id", "benchmark_results.entry_id"],
        ),
        CheckConstraint(
            "data_classification = 'non-production'",
            name="ck_benchmark_candidate_non_production",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    entry_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    parser_side: Mapped[str] = mapped_column(String(12), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    value_json: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(80))
    locator: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_classification: Mapped[str] = mapped_column(
        String(20), server_default="non-production"
    )


class BenchmarkDifferenceReviewRecord(Base):
    __tablename__ = "benchmark_difference_reviews"
    __table_args__ = (
        CheckConstraint(
            "state IN ('unreviewed', 'correct', 'incorrect', 'ambiguous')",
            name="ck_benchmark_review_state",
        ),
        CheckConstraint(
            "data_classification = 'non-production'",
            name="ck_benchmark_review_non_production",
        ),
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey("benchmark_runs.id"), primary_key=True
    )
    signature: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(12))
    candidate_adds_value: Mapped[bool]
    reason: Mapped[str] = mapped_column(String(1000))
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_classification: Mapped[str] = mapped_column(
        String(20), server_default="non-production"
    )


class SourceRuntimeStateRecord(Base):
    __tablename__ = "source_runtime_state"

    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    policy_version: Mapped[str] = mapped_column(String(80))
    day_key: Mapped[str] = mapped_column(String(10))
    attempt_count: Mapped[int] = mapped_column(server_default="0")
    success_count: Mapped[int] = mapped_column(server_default="0")
    next_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    direct_denial_count: Mapped[int] = mapped_column(server_default="0")


class ProxyUsageLedgerRecord(Base):
    __tablename__ = "proxy_usage_ledger"

    billing_cycle: Mapped[str] = mapped_column(String(7), primary_key=True)
    allocated_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")
    transferred_bytes: Mapped[int] = mapped_column(BigInteger, server_default="0")


class ProxyRouteHealthRecord(Base):
    __tablename__ = "proxy_route_health"

    route_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    healthy: Mapped[bool]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProxySourceQuarantineRecord(Base):
    __tablename__ = "proxy_source_quarantine"

    route_id: Mapped[str] = mapped_column(
        ForeignKey("proxy_route_health.route_id"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RedirectHandoffRecord(Base):
    """Validated cross-source target awaiting its own portal pipeline."""

    __tablename__ = "redirect_handoffs"
    __table_args__ = (
        UniqueConstraint("source_task_id", "target_source", "target_listing_id"),
        CheckConstraint(
            "source != target_source", name="ck_redirect_handoff_cross_source"
        ),
        CheckConstraint(
            "state IN ('pending', 'enqueued')", name="ck_redirect_handoff_state"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_task_id: Mapped[UUID] = mapped_column(ForeignKey("scrape_tasks.id"))
    source: Mapped[str] = mapped_column(String(20))
    target_source: Mapped[str] = mapped_column(String(20), index=True)
    target_listing_id: Mapped[str] = mapped_column(String(255))
    canonical_url: Mapped[str] = mapped_column(String(2048))
    state: Mapped[str] = mapped_column(String(20), server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
