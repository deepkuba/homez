from datetime import datetime, timezone
from decimal import Decimal

from homefinder.domain.matching import TriState
from homefinder.enrichment.renovation import (
    AttachmentMetadata,
    Comparable,
    HabitabilityChecklist,
    RenovationEstimate,
    RenovationItem,
    RenovationWorkflow,
)

AT = datetime(2026, 8, 31, tzinfo=timezone.utc)


def test_itemized_estimate_exposes_ranges_and_high_contingency() -> None:
    estimate = RenovationEstimate(
        (
            RenovationItem("electrics", 1_000_000, 2_000_000, 3_000_000),
            RenovationItem("bathroom", 2_000_000, 3_000_000, 4_000_000),
        ),
        contingency_rate=Decimal("0.15"),
    )

    assert estimate.low_minor == 3_000_000
    assert estimate.base_minor == 5_000_000
    assert estimate.high_minor == 7_000_000
    assert estimate.contingency_minor == 1_050_000
    assert estimate.high_with_contingency_minor == 8_050_000


def test_strong_comparables_can_pass_only_against_high_renovation_cost() -> None:
    estimate = RenovationEstimate(
        (RenovationItem("finish", 1_000_000, 2_000_000, 5_000_000),),
        contingency_rate=Decimal("0.10"),
    )
    assessment = RenovationWorkflow().assess(
        purchase_price_minor=70_000_000,
        mandatory_extras_minor=0,
        estimate=estimate,
        comparables=(
            Comparable("c1", 76_000_000, Decimal("0.90"), "registry", AT),
            Comparable("c2", 77_000_000, Decimal("0.85"), "registry", AT),
        ),
    )

    assert assessment.adjusted_advantage_minor == 1_000_000
    assert assessment.normal_eligibility is TriState.PASS
    assert assessment.confidence >= Decimal("0.80")


def test_weak_comparables_require_manual_review_and_never_pass() -> None:
    assessment = RenovationWorkflow().assess(
        purchase_price_minor=70_000_000,
        estimate=RenovationEstimate(()),
        comparables=(Comparable("weak", 100_000_000, Decimal("0.40"), "listing", AT),),
    )

    assert assessment.normal_eligibility is TriState.UNKNOWN
    assert assessment.manual_review
    assert "similar" in assessment.explanation


def test_habitability_checklist_and_attachment_metadata_are_safe() -> None:
    checklist = HabitabilityChecklist(
        utilities_connected=True,
        bathroom_usable=False,
        kitchen_usable=None,
        heating_operational=True,
        windows_secure=True,
    )
    assert not checklist.complete
    assert checklist.missing == ("bathroom_usable", "kitchen_usable")

    attachment = AttachmentMetadata(
        "quote-1", "contractor_quote", "quote.pdf", "application/pdf", 1024, "a" * 64
    )
    assert attachment.storage_key == "quote-1"
