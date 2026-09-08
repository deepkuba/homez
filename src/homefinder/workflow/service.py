"""Idempotent catalog-to-ranked-report orchestration."""

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    CandidateFactSetRecord,
    CandidateMatchEvaluationRecord,
    CandidatePresentationRecord,
    FeedbackEventRecord,
    ListingRecord,
    ListingSnapshotRecord,
    ReportDraftRecord,
    ReportItemRecord,
    SourceMessageItemRecord,
    SourceRecord,
)
from homefinder.catalog.profile_repository import SqlAlchemyBuyerProfileRepository
from homefinder.digest.render import Digest, DigestItem, render_digest
from homefinder.domain.costs import CostEstimate
from homefinder.domain.matching import (
    MatchExplanation,
    PropertyFacts,
    TransactionType,
    TriState,
    evaluate,
)
from homefinder.domain.profile import BuyerProfile
from homefinder.domain.ranking import RankedCandidate, select_slate
from homefinder.sources.portal_pages import ScrapedListing, extract_recurring_cost_facts
from homefinder.sources.remote_scraper import RemoteScrapeDeferred
from homefinder.workflow.models import (
    ClaimedJob,
    ManualReviewRequired,
    PermanentWorkflowError,
)
from homefinder.workflow.repository import WorkflowRepository

NORMALIZER_VERSION = "catalog-page-v2"
MATCHER_VERSION = "rules-v2"
SELECTION_VERSION = "slate-v2"
RENDER_VERSION = "digest-v3"
REPORT_NAMESPACE = UUID("7e8efea1-64da-4ba1-9a47-f70e23775994")


class WorkflowService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        pollers: Mapping[str, Callable[[], object]] | None = None,
        listing_scrapers: Mapping[str, Callable[[str], ScrapedListing]] | None = None,
    ) -> None:
        self._sessions = sessions
        self._pollers = dict(pollers or {})
        self._listing_scrapers = dict(listing_scrapers or {})
        self.jobs = WorkflowRepository(sessions)

    def enqueue_poll(self, *, source_key: str, scheduled_at: datetime) -> UUID:
        slot = _aware(scheduled_at).replace(second=0, microsecond=0)
        return self.jobs.enqueue(
            kind="poll",
            idempotency_key=f"poll:{source_key}:{slot.isoformat()}",
            payload={"source_key": source_key, "scheduled_at": slot.isoformat()},
            available_at=slot,
        )

    def reconcile_catalog(self, *, now: datetime) -> int:
        enqueued = 0
        with self._sessions() as session:
            items = session.scalars(select(SourceMessageItemRecord)).all()
            fact_sets = session.scalars(
                select(CandidateFactSetRecord).where(
                    CandidateFactSetRecord.normalizer_version == NORMALIZER_VERSION
                )
            ).all()
            try:
                active_profile_version = (
                    SqlAlchemyBuyerProfileRepository(session).active().version
                )
            except LookupError:
                active_profile_version = None
        facts_by_snapshot = {item.snapshot_id: item for item in fact_sets}
        for item in items:
            payload: dict[str, object] = {
                "candidate_id": str(item.candidate_id),
                "listing_id": str(item.listing_id),
                "snapshot_id": str(item.snapshot_id),
            }
            if item.snapshot_id not in facts_by_snapshot:
                self.jobs.enqueue(
                    kind="normalize",
                    idempotency_key=f"normalize:{item.snapshot_id}:{NORMALIZER_VERSION}",
                    payload=payload,
                    available_at=now,
                )
            else:
                fact_set = facts_by_snapshot[item.snapshot_id]
                if active_profile_version is None:
                    self.jobs.enqueue(
                        kind="enrich",
                        idempotency_key=f"enrich:{fact_set.id}:evidence-v1",
                        payload={**payload, "fact_set_id": str(fact_set.id)},
                        available_at=now,
                    )
                else:
                    self.jobs.enqueue(
                        kind="match",
                        idempotency_key=(
                            f"match:{fact_set.id}:{active_profile_version}:"
                            f"1:{MATCHER_VERSION}"
                        ),
                        payload={
                            **payload,
                            "fact_set_id": str(fact_set.id),
                            "buyer_profile_version": active_profile_version,
                            "routing_goal_version": 1,
                        },
                        available_at=now,
                    )
            enqueued += 1
        return enqueued

    def enqueue_report(
        self,
        *,
        period: str,
        cutoff_at: datetime,
        routing_goal_version: int,
    ) -> UUID:
        return self.jobs.enqueue(
            kind="report",
            idempotency_key=(
                f"report:{period}:{_aware(cutoff_at).isoformat()}:"
                f"{routing_goal_version}:{SELECTION_VERSION}"
            ),
            payload={
                "period": period,
                "cutoff_at": _aware(cutoff_at).isoformat(),
                "routing_goal_version": routing_goal_version,
            },
            available_at=cutoff_at,
        )

    def run_once(self, *, worker_id: str, now: datetime) -> bool:
        job = self.jobs.claim(worker_id=worker_id, now=now)
        if job is None:
            return False
        try:
            if job.kind == "poll":
                self._poll(job)
            elif job.kind == "normalize":
                self._normalize(job, now=now)
            elif job.kind == "enrich":
                self._enrich(job, now=now)
            elif job.kind == "match":
                self._match(job, now=now)
            elif job.kind == "report":
                self.prepare_report(
                    period=_text(job.payload, "period"),
                    cutoff_at=datetime.fromisoformat(_text(job.payload, "cutoff_at")),
                    routing_goal_version=_integer(job.payload, "routing_goal_version"),
                    now=now,
                )
            else:
                raise PermanentWorkflowError("unknown workflow job kind")
        except RemoteScrapeDeferred as error:
            self.jobs.defer(
                job,
                now=now,
                available_at=now + timedelta(seconds=error.retry_after_seconds),
                code="portal-rate-limited",
                detail="portal request deferred",
            )
        except ManualReviewRequired as error:
            self.jobs.fail(
                job,
                now=now,
                code="manual-review",
                detail=str(error),
                retryable=False,
                manual_review=True,
            )
        except PermanentWorkflowError as error:
            self.jobs.fail(
                job,
                now=now,
                code="permanent-error",
                detail=str(error),
                retryable=False,
            )
        except Exception:
            self.jobs.fail(
                job,
                now=now,
                code="workflow-stage-failed",
                detail="stage failed; inspect structured operational logs",
            )
        else:
            self.jobs.succeed(job, now=now)
        return True

    def run_until_idle(
        self, *, worker_id: str, now: datetime, max_jobs: int = 1_000
    ) -> int:
        processed = 0
        while processed < max_jobs and self.run_once(worker_id=worker_id, now=now):
            processed += 1
        return processed

    def _poll(self, job: ClaimedJob) -> None:
        source_key = _text(job.payload, "source_key")
        poller = self._pollers.get(source_key)
        if poller is None:
            raise PermanentWorkflowError(
                "poll source is not configured for this worker"
            )
        poller()

    def _normalize(self, job: ClaimedJob, *, now: datetime) -> None:
        candidate_id = UUID(_text(job.payload, "candidate_id"))
        listing_id = UUID(_text(job.payload, "listing_id"))
        snapshot_id = UUID(_text(job.payload, "snapshot_id"))
        with self._sessions() as session:
            existing = session.scalar(
                select(CandidateFactSetRecord).where(
                    CandidateFactSetRecord.candidate_id == candidate_id,
                    CandidateFactSetRecord.snapshot_id == snapshot_id,
                    CandidateFactSetRecord.normalizer_version == NORMALIZER_VERSION,
                )
            )
            if existing is None:
                listing = session.get(ListingRecord, listing_id)
                snapshot = session.get(ListingSnapshotRecord, snapshot_id)
                if listing is None or snapshot is None:
                    raise PermanentWorkflowError("catalog input is missing")
                source = session.get(SourceRecord, listing.source_id)
                if source is None:
                    raise PermanentWorkflowError("listing source is missing")
                scraped = self._scrape_listing(source.key, listing)
                payload = {
                    "candidate_id": str(candidate_id),
                    "listing_id": str(listing_id),
                    "snapshot_id": str(snapshot_id),
                    "title": scraped.title if scraped is not None else listing.title,
                    "canonical_url": listing.canonical_url,
                    "locality": (
                        scraped.location
                        if scraped is not None and scraped.location is not None
                        else snapshot.location
                    ),
                    "purchase_price_minor": (
                        scraped.price_minor
                        if scraped is not None and scraped.price_minor is not None
                        else snapshot.price_minor
                    ),
                    "currency": (
                        scraped.currency
                        if scraped is not None and scraped.currency is not None
                        else snapshot.currency
                    ),
                    "area_sqm": (
                        str(scraped.area_sqm)
                        if scraped is not None and scraped.area_sqm is not None
                        else str(snapshot.area_sqm)
                        if snapshot.area_sqm is not None
                        else None
                    ),
                    "rooms": (
                        scraped.rooms
                        if scraped is not None and scraped.rooms is not None
                        else snapshot.rooms
                    ),
                    "availability": (
                        scraped.availability
                        if scraped is not None and scraped.availability != "unknown"
                        else snapshot.availability
                    ),
                    "description": (
                        scraped.description
                        if scraped is not None and scraped.description
                        else snapshot.description
                    ),
                    "monthly_admin_fee_minor": (
                        scraped.monthly_admin_fee_minor if scraped is not None else None
                    ),
                    "heating_type": (
                        scraped.heating_type if scraped is not None else None
                    ),
                    "admin_fee_includes_heating": (
                        scraped.admin_fee_includes_heating
                        if scraped is not None
                        else None
                    ),
                }
                encoded = _canonical(payload)
                facts_hash = _sha(encoded)
                material = _sha(
                    _canonical(
                        {
                            key: payload[key]
                            for key in (
                                "canonical_url",
                                "purchase_price_minor",
                                "currency",
                                "area_sqm",
                                "rooms",
                                "availability",
                                "locality",
                                "description",
                                "monthly_admin_fee_minor",
                                "heating_type",
                                "admin_fee_includes_heating",
                            )
                        }
                    )
                )
                existing = CandidateFactSetRecord(
                    id=uuid5(REPORT_NAMESPACE, f"facts:{facts_hash}"),
                    candidate_id=candidate_id,
                    listing_id=listing_id,
                    snapshot_id=snapshot_id,
                    normalizer_version=NORMALIZER_VERSION,
                    facts_schema_version=1,
                    facts_json=encoded,
                    facts_hash=facts_hash,
                    material_fingerprint=material,
                    created_at=now,
                )
                session.add(existing)
                session.commit()
            fact_set_id = existing.id
        self.jobs.enqueue(
            kind="enrich",
            idempotency_key=f"enrich:{fact_set_id}:evidence-v1",
            payload={
                "candidate_id": str(candidate_id),
                "listing_id": str(listing_id),
                "snapshot_id": str(snapshot_id),
                "fact_set_id": str(fact_set_id),
            },
            available_at=now,
            parent_job_id=job.id,
        )

    def _scrape_listing(
        self, source_key: str, listing: ListingRecord
    ) -> ScrapedListing | None:
        scraper = self._listing_scrapers.get(source_key)
        if scraper is None:
            return None
        scraped = scraper(listing.canonical_url)
        if (
            scraped.source_key != source_key
            or scraped.canonical_url != listing.canonical_url
        ):
            raise PermanentWorkflowError("scraped listing identity is inconsistent")
        return scraped

    def _enrich(self, job: ClaimedJob, *, now: datetime) -> None:
        fact_set_id = UUID(_text(job.payload, "fact_set_id"))
        with self._sessions() as session:
            if session.get(CandidateFactSetRecord, fact_set_id) is None:
                raise PermanentWorkflowError("normalized fact set is missing")
            try:
                profile_version = (
                    SqlAlchemyBuyerProfileRepository(session).active().version
                )
            except LookupError as error:
                raise ManualReviewRequired(
                    "approved buyer profile is required before matching"
                ) from error
        self.jobs.enqueue(
            kind="match",
            idempotency_key=(
                f"match:{fact_set_id}:{profile_version}:1:{MATCHER_VERSION}"
            ),
            payload={
                **job.payload,
                "buyer_profile_version": profile_version,
                "routing_goal_version": 1,
            },
            available_at=now,
            parent_job_id=job.id,
        )

    def _match(self, job: ClaimedJob, *, now: datetime) -> None:
        fact_set_id = UUID(_text(job.payload, "fact_set_id"))
        routing_goal_version = _integer(job.payload, "routing_goal_version")
        requested_profile_version = _integer(job.payload, "buyer_profile_version")
        with self._sessions() as session:
            fact_set = session.get(CandidateFactSetRecord, fact_set_id)
            if fact_set is None:
                raise PermanentWorkflowError("normalized fact set is missing")
            try:
                profile = SqlAlchemyBuyerProfileRepository(session).active()
            except LookupError as error:
                raise ManualReviewRequired(
                    "approved buyer profile is required before matching"
                ) from error
            if profile.version != requested_profile_version:
                raise PermanentWorkflowError(
                    "workflow profile version is no longer active"
                )
            input_hash = _sha(
                f"{fact_set.facts_hash}:{profile.version}:"
                f"{routing_goal_version}:{MATCHER_VERSION}"
            )
            if session.scalar(
                select(CandidateMatchEvaluationRecord).where(
                    CandidateMatchEvaluationRecord.input_fingerprint == input_hash
                )
            ):
                return
            payload = json.loads(fact_set.facts_json)
            facts = _facts_from_payload(payload, session, fact_set)
            explanation = evaluate(facts, profile)
            session.add(
                CandidateMatchEvaluationRecord(
                    id=uuid5(REPORT_NAMESPACE, f"match:{input_hash}"),
                    candidate_id=fact_set.candidate_id,
                    listing_id=fact_set.listing_id,
                    snapshot_id=fact_set.snapshot_id,
                    fact_set_id=fact_set.id,
                    buyer_profile_version=profile.version,
                    routing_goal_version=routing_goal_version,
                    matcher_version=MATCHER_VERSION,
                    input_fingerprint=input_hash,
                    facts_json=_serialize_facts(facts),
                    explanation_json=_serialize_explanation(explanation),
                    eligible=explanation.eligible,
                    contains_unknown_hard_rule=any(
                        rule.state is TriState.UNKNOWN
                        for rule in explanation.eligibility
                    ),
                    score=explanation.score,
                    confidence=explanation.confidence,
                    evaluated_at=now,
                )
            )
            session.commit()

    def prepare_report(
        self,
        *,
        period: str,
        cutoff_at: datetime,
        routing_goal_version: int,
        now: datetime,
    ) -> UUID:
        with self._sessions() as session:
            try:
                profile = SqlAlchemyBuyerProfileRepository(session).active()
            except LookupError as error:
                raise ManualReviewRequired(
                    "approved buyer profile is required before report preparation"
                ) from error
            report_key = _sha(
                f"{period}:{_aware(cutoff_at).isoformat()}:{profile.version}:"
                f"{routing_goal_version}:{SELECTION_VERSION}:{RENDER_VERSION}"
            )
            existing = session.scalar(
                select(ReportDraftRecord).where(
                    ReportDraftRecord.report_key == report_key
                )
            )
            if existing is not None:
                return existing.id
            evaluations = session.scalars(
                select(CandidateMatchEvaluationRecord)
                .where(
                    CandidateMatchEvaluationRecord.buyer_profile_version
                    == profile.version,
                    CandidateMatchEvaluationRecord.routing_goal_version
                    == routing_goal_version,
                    CandidateMatchEvaluationRecord.evaluated_at <= cutoff_at,
                )
                .order_by(
                    CandidateMatchEvaluationRecord.evaluated_at.desc(),
                    CandidateMatchEvaluationRecord.id.desc(),
                )
            ).all()
            latest: dict[UUID, CandidateMatchEvaluationRecord] = {}
            feedback_listing_ids = set(
                session.scalars(select(FeedbackEventRecord.listing_id)).all()
            )
            for evaluation_record in evaluations:
                if str(evaluation_record.listing_id) in feedback_listing_ids:
                    continue
                latest.setdefault(evaluation_record.candidate_id, evaluation_record)
            candidates = [
                _ranked_from_record(session, record, profile)
                for record in latest.values()
            ]
            slate = select_slate(
                [candidate.facts for candidate in candidates],
                profile,
                limit=10,
                now=now,
            )
            urls: dict[str, ListingRecord] = {}
            for record in latest.values():
                listing = session.get(ListingRecord, record.listing_id)
                if listing is None:
                    raise PermanentWorkflowError("match references missing listing")
                urls[str(record.candidate_id)] = listing
            report_id = uuid5(REPORT_NAMESPACE, f"report:{report_key}")
            digest = Digest(
                report_id=str(report_id),
                generated_at=cutoff_at,
                compliant=tuple(
                    DigestItem(item, urls[item.facts.id].canonical_url)
                    for item in slate.compliant
                ),
                exploration=tuple(
                    DigestItem(item, urls[item.facts.id].canonical_url)
                    for item in slate.exploration
                ),
            )
            html, text = render_digest(digest)
            session.add(
                ReportDraftRecord(
                    id=report_id,
                    report_key=report_key,
                    period=period,
                    cutoff_at=cutoff_at,
                    buyer_profile_version=profile.version,
                    routing_goal_version=routing_goal_version,
                    selection_version=SELECTION_VERSION,
                    render_version=RENDER_VERSION,
                    status="prepared",
                    html_body=html,
                    text_body=text,
                    content_hash=_sha(html + "\0" + text),
                    created_at=now,
                    prepared_at=now,
                )
            )
            for section, values in (
                ("compliant", slate.compliant),
                ("exploration", slate.exploration),
            ):
                for position, item in enumerate(values):
                    record = latest[UUID(item.facts.id)]
                    fact_set = session.get(CandidateFactSetRecord, record.fact_set_id)
                    listing = session.get(ListingRecord, record.listing_id)
                    if fact_set is None or listing is None:
                        raise PermanentWorkflowError("report input disappeared")
                    session.add(
                        ReportItemRecord(
                            report_id=report_id,
                            section=section,
                            position=position,
                            candidate_id=record.candidate_id,
                            listing_id=record.listing_id,
                            snapshot_id=record.snapshot_id,
                            evaluation_id=record.id,
                            canonical_url=listing.canonical_url,
                            material_fingerprint=fact_set.material_fingerprint,
                            selection_reason=(
                                "; ".join(item.explanation.exploration_reasons)
                                or "all hard rules pass"
                            ),
                        )
                    )
            session.commit()
            return report_id


def _facts_from_payload(
    payload: dict[str, object],
    session: Session,
    fact_set: CandidateFactSetRecord,
) -> PropertyFacts:
    heating_included = payload.get("admin_fee_includes_heating")
    description = str(payload.get("description") or "")
    inferred_fee, inferred_heating, inferred_inclusion = extract_recurring_cost_facts(
        description
    )
    presentation = session.scalar(
        select(CandidatePresentationRecord)
        .where(CandidatePresentationRecord.candidate_id == fact_set.candidate_id)
        .order_by(CandidatePresentationRecord.presented_at.desc())
    )
    material_changed = bool(
        presentation is not None
        and (
            presentation.material_fingerprint is not None
            and presentation.material_fingerprint != fact_set.material_fingerprint
            or presentation.material_fingerprint is None
            and presentation.snapshot_id != fact_set.snapshot_id
        )
    )
    return PropertyFacts(
        id=str(fact_set.candidate_id),
        title=str(payload["title"]),
        locality=(str(payload["locality"]) if payload.get("locality") else None),
        cost=(
            CostEstimate(
                purchase_price_minor=_object_int(
                    payload.get("purchase_price_minor"), "purchase_price_minor"
                )
            )
            if payload.get("purchase_price_minor") is not None
            else None
        ),
        area_sqm=(
            Decimal(str(payload["area_sqm"]))
            if payload.get("area_sqm") is not None
            else None
        ),
        rooms=(
            _object_int(payload.get("rooms"), "rooms")
            if payload.get("rooms") is not None
            else None
        ),
        monthly_admin_fee_minor=(
            _object_int(
                payload.get("monthly_admin_fee_minor"), "monthly_admin_fee_minor"
            )
            if payload.get("monthly_admin_fee_minor") is not None
            else inferred_fee
        ),
        heating_type=(
            str(payload["heating_type"])
            if payload.get("heating_type") is not None
            else inferred_heating
        ),
        admin_fee_includes_heating=(
            heating_included
            if isinstance(heating_included, bool)
            else inferred_inclusion
        ),
        transaction_type=TransactionType.PURCHASE,
        market_type=None,
        last_presented_at=(
            presentation.presented_at if presentation is not None else None
        ),
        materially_changed=material_changed,
    )


def _ranked_from_record(
    session: Session,
    record: CandidateMatchEvaluationRecord,
    profile: BuyerProfile,
) -> RankedCandidate:
    fact_set = session.get(CandidateFactSetRecord, record.fact_set_id)
    if fact_set is None:
        raise PermanentWorkflowError("match references missing facts")
    facts = _facts_from_payload(json.loads(fact_set.facts_json), session, fact_set)
    return RankedCandidate(facts, evaluate(facts, profile))


def _serialize_facts(facts: PropertyFacts) -> str:
    return _canonical(
        {
            "id": facts.id,
            "title": facts.title,
            "locality": facts.locality,
            "area_sqm": str(facts.area_sqm) if facts.area_sqm is not None else None,
            "rooms": facts.rooms,
            "transaction_type": (
                facts.transaction_type.value if facts.transaction_type else None
            ),
            "market_type": facts.market_type.value if facts.market_type else None,
            "monthly_admin_fee_minor": facts.monthly_admin_fee_minor,
            "heating_type": facts.heating_type,
            "admin_fee_includes_heating": facts.admin_fee_includes_heating,
        }
    )


def _serialize_explanation(value: MatchExplanation) -> str:
    return _canonical(
        {
            "eligible": value.eligible,
            "score": str(value.score),
            "confidence": str(value.confidence),
            "reasons": list(value.exploration_reasons),
            "rules": [
                {
                    "name": rule.name,
                    "state": rule.state.value,
                    "actual": rule.actual,
                    "threshold": rule.threshold,
                    "distance": rule.distance,
                }
                for rule in value.eligibility
            ],
            "preferences": [
                {
                    "name": rule.name,
                    "state": rule.state.value,
                    "actual": rule.actual,
                    "threshold": rule.threshold,
                    "distance": rule.distance,
                }
                for rule in value.preferences
            ],
        }
    )


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise PermanentWorkflowError(f"workflow payload field {key} is invalid")
    return value


def _integer(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise PermanentWorkflowError(f"workflow payload field {key} is invalid")
    return value


def _object_int(value: object, key: str) -> int:
    if not isinstance(value, int):
        raise PermanentWorkflowError(f"normalized fact field {key} is invalid")
    return value
