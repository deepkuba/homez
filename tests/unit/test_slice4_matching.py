from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from homefinder.domain.costs import CostEstimate
from homefinder.domain.matching import (
    MarketType,
    PropertyFacts,
    TransactionType,
    TriState,
    evaluate,
)
from homefinder.domain.profile import BuyerProfile
from homefinder.domain.ranking import select_slate


def _facts(identifier: str = "home-1", **changes: object) -> PropertyFacts:
    values: dict[str, object] = {
        "id": identifier,
        "locality": "Kraków",
        "cost": CostEstimate(
            purchase_price_minor=70_000_000,
            closing_costs_minor=1_000_000,
            financed_purchase_value_minor=55_000_000,
            monthly_installment_minor=350_000,
        ),
        "area_sqm": Decimal("52"),
        "rooms": 2,
        "market_type": MarketType.SECONDARY,
        "vacant_possession": True,
        "separate_ownership": True,
        "serious_legal_risk": False,
        "floor": 2,
        "building_top_floor": 4,
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
        _facts(
            cost=CostEstimate(
                purchase_price_minor=80_000_001,
                financed_purchase_value_minor=65_000_001,
                monthly_installment_minor=350_000,
            ),
            commute_minutes=None,
        ),
        BuyerProfile(),
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
    assert "actual PLN 800,000.01" in next(
        rule.explanation for rule in result.eligibility if rule.name == "price"
    )
    assert "over by PLN 0.01" in next(
        rule.explanation for rule in result.eligibility if rule.name == "price"
    )


def test_costs_use_high_renovation_and_contingency_for_effective_price() -> None:
    estimate = CostEstimate(
        70_000_000,
        2_000_000,
        1_500_000,
        3_000_000,
        5_000_000,
        10_000_000,
        1_000_000,
        60_000_000,
        400_000,
    )

    assert estimate.acquisition_price_minor == 72_000_000
    assert estimate.effective_all_in_high_minor == 84_500_000
    assert estimate.acquisition_cash_high_minor == 24_500_000
    assert estimate.affordability(24_500_000, 400_000)


def test_primary_market_purchase_can_pass_with_complete_dossier() -> None:
    result = evaluate(
        _facts(
            transaction_type=TransactionType.PURCHASE,
            market_type=MarketType.PRIMARY,
            primary_market_eligibility=TriState.PASS,
        ),
        BuyerProfile(),
    )

    assert result.eligible
    assert (
        next(rule for rule in result.eligibility if rule.name == "transaction").state
        is TriState.PASS
    )
    assert (
        next(
            rule
            for rule in result.eligibility
            if rule.name == "primary_market_evidence"
        ).state
        is TriState.PASS
    )


@pytest.mark.parametrize(
    ("changes", "rule_name", "passing", "failing", "distance"),
    [
        (
            {
                "cost": CostEstimate(
                    purchase_price_minor=80_000_000,
                    financed_purchase_value_minor=65_000_000,
                    monthly_installment_minor=400_000,
                )
            },
            "price",
            TriState.PASS,
            {
                "cost": CostEstimate(
                    purchase_price_minor=80_000_001,
                    financed_purchase_value_minor=65_000_001,
                    monthly_installment_minor=400_000,
                )
            },
            "over by PLN 0.01",
        ),
        (
            {
                "cost": CostEstimate(
                    purchase_price_minor=70_000_000,
                    financed_purchase_value_minor=50_000_000,
                    monthly_installment_minor=400_000,
                )
            },
            "cash",
            TriState.PASS,
            {
                "cost": CostEstimate(
                    purchase_price_minor=70_000_001,
                    financed_purchase_value_minor=50_000_000,
                    monthly_installment_minor=400_000,
                )
            },
            "over by PLN 0.01",
        ),
        (
            {"area_sqm": Decimal("40")},
            "area",
            TriState.PASS,
            {"area_sqm": Decimal("39.99")},
            "below by 0.01 m²",
        ),
        ({"rooms": 2}, "rooms", TriState.PASS, {"rooms": 1}, "below by 1 room"),
        (
            {"floor": 3, "building_top_floor": 4},
            "floor",
            TriState.PASS,
            {"floor": 4, "building_top_floor": 4},
            "top floor",
        ),
        (
            {"floor": 3, "building_top_floor": 4, "has_elevator": None},
            "elevator",
            TriState.PASS,
            {"floor": 4, "building_top_floor": 5, "has_elevator": False},
            "does not meet requirement",
        ),
        (
            {"commute_minutes": 45},
            "commute",
            TriState.PASS,
            {"commute_minutes": 46},
            "over by 1 minute",
        ),
        (
            {"separate_ownership": True},
            "separate_ownership",
            TriState.PASS,
            {"separate_ownership": False},
            "actual false",
        ),
    ],
)
def test_hard_rule_boundaries_report_actual_threshold_and_distance(
    changes: dict[str, object],
    rule_name: str,
    passing: TriState,
    failing: dict[str, object],
    distance: str,
) -> None:
    passing_rule = next(
        rule
        for rule in evaluate(_facts(**changes), BuyerProfile()).eligibility
        if rule.name == rule_name
    )
    failing_rule = next(
        rule
        for rule in evaluate(_facts(**failing), BuyerProfile()).eligibility
        if rule.name == rule_name
    )

    assert passing_rule.state is passing
    assert failing_rule.state is TriState.FAIL
    assert failing_rule.actual != ""
    assert failing_rule.threshold != ""
    assert distance in failing_rule.distance


def test_unknown_hard_rule_exposes_unknown_actual_and_threshold() -> None:
    rule = next(
        item
        for item in evaluate(_facts(commute_minutes=None), BuyerProfile()).eligibility
        if item.name == "commute"
    )

    assert rule.state is TriState.UNKNOWN
    assert rule.actual == "unknown"
    assert rule.threshold == "at most 45 minutes"
    assert "distance unknown" in rule.explanation


def test_exploration_names_every_failed_and_unknown_deviation() -> None:
    result = evaluate(
        _facts(
            area_sqm=Decimal("39"),
            commute_minutes=None,
            separate_ownership=False,
        ),
        BuyerProfile(),
    )

    assert {
        reason.split(":", maxsplit=1)[0] for reason in result.exploration_reasons
    } == {"separate_ownership", "area", "commute"}
    assert all(
        "actual " in reason and "threshold " in reason
        for reason in result.exploration_reasons
    )


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


def test_both_slate_sections_are_diversified_and_obey_resurfacing_policy() -> None:
    now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    properties = [
        _facts("good-krakow-1", locality="Kraków"),
        _facts("good-krakow-2", locality="Kraków"),
        _facts("good-wieliczka", locality="Wieliczka"),
        _facts("bad-krakow-1", locality="Kraków", separate_ownership=False),
        _facts("bad-krakow-2", locality="Kraków", separate_ownership=False),
        _facts("bad-wieliczka", locality="Wieliczka", separate_ownership=False),
        _facts(
            "cooldown-good",
            locality="Bochnia",
            last_presented_at=now - timedelta(days=1),
        ),
        _facts(
            "cooldown-bad",
            locality="Tarnów",
            separate_ownership=False,
            last_presented_at=now - timedelta(days=1),
        ),
        _facts(
            "changed-bad",
            locality="Niepołomice",
            separate_ownership=False,
            last_presented_at=now - timedelta(days=1),
            materially_changed=True,
        ),
    ]

    slate = select_slate(
        properties, BuyerProfile(), limit=3, now=now, cooldown=timedelta(days=7)
    )

    assert [item.facts.locality for item in slate.compliant[:2]] == [
        "Kraków",
        "Wieliczka",
    ]
    assert len({item.facts.locality for item in slate.exploration}) == 3
    selected_ids = {item.facts.id for item in (*slate.compliant, *slate.exploration)}
    assert "cooldown-good" not in selected_ids
    assert "cooldown-bad" not in selected_ids
    assert "changed-bad" in selected_ids
