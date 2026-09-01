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
    period: Mapped[str] = mapped_column(String(8), index=True)
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

    period: Mapped[str] = mapped_column(String(8), primary_key=True)
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
