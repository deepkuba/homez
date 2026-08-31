from datetime import datetime, timezone

from homefinder.domain.costs import CostEstimate
from homefinder.domain.matching import MarketType, PropertyFacts, TriState, evaluate
from homefinder.enrichment.primary_market import (
    CriticalCheck,
    EntityRole,
    Evidence,
    EvidenceKind,
    ManualTaskQueue,
    PrimaryMarketDossier,
    ProjectEntity,
    RiskAssessment,
    RiskDimension,
    RiskLevel,
)

AT = datetime(2026, 8, 31, tzinfo=timezone.utc)


def dossier(
    *, critical: TriState = TriState.PASS, legal: RiskLevel = RiskLevel.LOWER_CONCERN
) -> PrimaryMarketDossier:
    return PrimaryMarketDossier(
        project_id="project-1",
        project_name="Example Residence",
        entities=(
            ProjectEntity(
                "spv-1",
                "Example Residence sp. z o.o.",
                EntityRole.CONTRACTING_SPV,
                "KRS-1",
            ),
            ProjectEntity("group-1", "Example Group", EntityRole.PARENT_GROUP, "KRS-2"),
        ),
        evidence=(
            Evidence(
                "e-1",
                "spv-1",
                EvidenceKind.KRS_EXTRACT,
                "KRS",
                AT,
                "krs/ref",
                True,
                "active filing",
            ),
        ),
        critical_checks=(CriticalCheck("prospectus", critical, "current prospectus"),),
        risks=(
            RiskAssessment(
                RiskDimension.LEGAL_PERMIT, legal, ("permit reviewed",), ("e-1",), AT
            ),
        ),
    )


def test_dossier_separates_spv_from_parent_and_blocks_unknown_critical_evidence() -> (
    None
):
    unknown = dossier(critical=TriState.UNKNOWN)
    assert unknown.normal_eligibility is TriState.UNKNOWN
    assert unknown.manual_review
    assert unknown.missing_critical_checks == ("prospectus",)
    assert unknown.entities[0].role is EntityRole.CONTRACTING_SPV
    assert unknown.entities[1].role is EntityRole.PARENT_GROUP


def test_serious_legal_risk_fails_even_when_other_evidence_is_complete() -> None:
    assessed = dossier(legal=RiskLevel.HIGHER_CONCERN)
    assert assessed.normal_eligibility is TriState.FAIL
    assert assessed.manual_review


def test_document_ingestion_requires_explicit_permission_and_queue_is_deduplicated():
    evidence = Evidence(
        "e-2",
        "project-1",
        EvidenceKind.PROSPECTUS,
        "buyer",
        AT,
        "doc/ref",
        True,
        "received document",
    )
    queue = ManualTaskQueue()
    first = queue.enqueue("project-1", "KRS", "verify current filing")
    second = queue.enqueue("project-1", "KRS", "verify current filing")
    assert first.id == second.id
    assert len(queue.pending()) == 1
    assert evidence.permitted


def test_primary_market_unknown_gate_is_reflected_in_matching() -> None:
    result = evaluate(
        PropertyFacts(
            "p1",
            market_type=MarketType.PRIMARY,
            primary_market_eligibility=TriState.UNKNOWN,
            cost=CostEstimate(
                70_000_000,
                financed_purchase_value_minor=55_000_000,
                monthly_installment_minor=350_000,
            ),
        ),
        __import__(
            "homefinder.domain.profile", fromlist=["BuyerProfile"]
        ).BuyerProfile(),
    )
    rule = next(
        item for item in result.eligibility if item.name == "primary_market_evidence"
    )
    assert rule.state is TriState.UNKNOWN
    assert not result.eligible
