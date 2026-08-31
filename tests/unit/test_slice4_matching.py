from decimal import Decimal

from homefinder.domain.costs import CostEstimate
from homefinder.domain.matching import PropertyFacts, TriState, evaluate
from homefinder.domain.profile import BuyerProfile
from homefinder.domain.ranking import select_slate


def _facts(identifier: str = "home-1", **changes: object) -> PropertyFacts:
    values: dict[str, object] = {
        "id": identifier,
        "locality": "Kraków",
        "price_minor": 70_000_000,
        "area_sqm": Decimal("52"),
        "rooms": 2,
        "vacant_possession": True,
        "separate_ownership": True,
        "serious_legal_risk": False,
        "floor": 2,
        "usable_layout": True,
        "building_dwellings": 18,
        "commute_minutes": 35,
        "parking_possible": True,
        "ready_to_move": True,
        "quiet": True,
        "green_space": True,
        "balcony": True,
        "separate_kitchen": True,
    }
    values.update(changes)
    return PropertyFacts(**values)


def test_golden_eligible_listing_has_transparent_score_and_confidence() -> None:
    result = evaluate(_facts(), BuyerProfile())

    assert result.eligible
    assert result.score == Decimal("100.00")
    assert result.confidence == Decimal("1.00")
    assert {component.name for component in result.components} == {
        "ready_to_move",
        "quiet",
        "green_space",
        "balcony",
        "separate_kitchen",
        "small_building",
        "area_fit",
    }


def test_failed_rule_cannot_be_overridden_by_score_and_unknown_stays_visible() -> None:
    result = evaluate(
        _facts(price_minor=81_000_000, commute_minutes=None), BuyerProfile()
    )

    assert not result.eligible
    assert result.score == Decimal("100.00")
    assert result.confidence < Decimal("1")
    assert (
        next(rule for rule in result.eligibility if rule.name == "price").state
        is TriState.FAIL
    )
    assert (
        next(rule for rule in result.eligibility if rule.name == "commute").state
        is TriState.UNKNOWN
    )
    assert any("price" in reason for reason in result.exploration_reasons)


def test_costs_use_high_renovation_and_contingency_for_effective_price() -> None:
    estimate = CostEstimate(
        70_000_000,
        2_000_000,
        1_500_000,
        3_000_000,
        5_000_000,
        10_000_000,
        1_000_000,
        400_000,
    )

    assert estimate.acquisition_price_minor == 72_000_000
    assert estimate.cash_needed_at_closing_minor == 73_500_000
    assert estimate.effective_all_in_high_minor == 83_000_000
    assert estimate.affordability(80_000_000, 400_000)


def test_slate_keeps_compliant_and_exploration_separate_and_caps_each_at_ten() -> None:
    properties = [_facts(f"good-{i}") for i in range(12)] + [
        _facts(f"bad-{i}", separate_ownership=False) for i in range(12)
    ]

    slate = select_slate(properties, BuyerProfile())

    assert len(slate.compliant) == 10
    assert len(slate.exploration) == 10
    assert all(item.explanation.eligible for item in slate.compliant)
    assert all(not item.explanation.eligible for item in slate.exploration)
    assert all(
        any(
            reason.startswith("separate_ownership:")
            for reason in item.explanation.exploration_reasons
        )
        for item in slate.exploration
    )
