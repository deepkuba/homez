"""Tri-state eligibility, confidence, and explainable match scoring."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from homefinder.domain.costs import CostEstimate
from homefinder.domain.profile import BuyerProfile


class TriState(str, Enum):
    PASS = "pass"  # noqa: S105 - a tri-state label, not a credential
    FAIL = "fail"
    UNKNOWN = "unknown"


class TransactionType(str, Enum):
    PURCHASE = "purchase"
    RENTAL = "rental"


class MarketType(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


@dataclass(frozen=True, slots=True)
class PropertyFacts:
    id: str
    title: str = ""
    locality: str | None = None
    cost: CostEstimate | None = None
    area_sqm: Decimal | None = None
    rooms: int | None = None
    transaction_type: TransactionType | None = TransactionType.PURCHASE
    market_type: MarketType | None = None
    primary_market_eligibility: TriState | None = None
    vacant_possession: bool | None = None
    separate_ownership: bool | None = None
    serious_legal_risk: bool | None = None
    floor: int | None = None
    building_top_floor: int | None = None
    has_private_garden: bool | None = None
    has_elevator: bool | None = None
    usable_layout: bool | None = None
    building_dwellings: int | None = None
    entrance_dwellings: int | None = None
    commute_minutes: int | None = None
    parking_possible: bool | None = None
    ready_to_move: bool | None = None
    quiet: bool | None = None
    green_space: bool | None = None
    balcony: bool | None = None
    separate_kitchen: bool | None = None
    score_confidence: Decimal = Decimal("1")
    last_presented_at: datetime | None = None
    materially_changed: bool = False


@dataclass(frozen=True, slots=True)
class RuleResult:
    name: str
    state: TriState
    actual: str
    threshold: str
    distance: str

    @property
    def explanation(self) -> str:
        return f"actual {self.actual}; threshold {self.threshold}; {self.distance}"


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    name: str
    value: Decimal
    weight: Decimal
    explanation: str


@dataclass(frozen=True, slots=True)
class MatchExplanation:
    eligibility: tuple[RuleResult, ...]
    components: tuple[ScoreComponent, ...]
    score: Decimal
    confidence: Decimal
    exploration_reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return all(rule.state is TriState.PASS for rule in self.eligibility)


def evaluate(facts: PropertyFacts, profile: BuyerProfile) -> MatchExplanation:
    cost = facts.cost
    rules = (
        _boolean_rule(
            "transaction",
            None
            if facts.transaction_type is None
            else facts.transaction_type == TransactionType.PURCHASE,
            "purchase",
            actual=(
                str(facts.transaction_type.value)
                if isinstance(facts.transaction_type, TransactionType)
                else str(facts.transaction_type)
                if facts.transaction_type is not None
                else "unknown"
            ),
        ),
        _state_rule(
            "primary_market_evidence",
            _primary_market_evidence(facts),
            "complete dossier with no disqualifying risk",
        ),
        _boolean_rule("vacant_possession", facts.vacant_possession, "true"),
        _boolean_rule("separate_ownership", facts.separate_ownership, "true"),
        _boolean_rule(
            "legal_risk",
            None if facts.serious_legal_risk is None else not facts.serious_legal_risk,
            "no serious legal risk",
            actual=(
                "unknown"
                if facts.serious_legal_risk is None
                else str(facts.serious_legal_risk).lower()
            ),
        ),
        _boolean_rule(
            "locality",
            None
            if facts.locality is None
            else facts.locality.casefold() not in profile.excluded_localities,
            "not an excluded locality",
            actual=facts.locality or "unknown",
        ),
        _money_max_rule(
            "price",
            cost.effective_all_in_high_minor if cost else None,
            profile.max_purchase_price_minor,
        ),
        _money_max_rule(
            "cash",
            cost.acquisition_cash_high_minor if cost else None,
            profile.cash_budget_minor,
        ),
        _money_max_rule(
            "installment",
            cost.monthly_installment_minor if cost else None,
            profile.max_monthly_installment_minor,
        ),
        _decimal_min_rule("area", facts.area_sqm, profile.min_area_sqm, "m²"),
        _integer_min_rule("rooms", facts.rooms, profile.min_rooms, "room"),
        _boolean_rule(
            "usable_layout",
            facts.usable_layout,
            "living room and separate usable room",
        ),
        _floor_rule(facts),
        _elevator_rule(facts),
        _boolean_rule("parking", facts.parking_possible, "parking possible"),
        _integer_max_rule(
            "building_scale",
            facts.building_dwellings,
            profile.max_building_dwellings,
            "dwelling",
        ),
        _integer_max_rule(
            "commute", facts.commute_minutes, profile.max_commute_minutes, "minute"
        ),
    )
    components = _score_components(facts, profile)
    score = sum(
        (component.value * component.weight for component in components), Decimal("0")
    )
    confidence = max(
        Decimal("0"),
        min(
            Decimal("1"),
            facts.score_confidence
            * Decimal(sum(r.state is not TriState.UNKNOWN for r in rules))
            / Decimal(len(rules)),
        ),
    )
    reasons = tuple(
        f"{rule.name}: {rule.explanation} ({rule.state.value})"
        for rule in rules
        if rule.state is not TriState.PASS
    )
    return MatchExplanation(
        rules,
        components,
        score.quantize(Decimal("0.01")),
        confidence.quantize(Decimal("0.01")),
        reasons,
    )


def _state_rule(name: str, state: TriState, threshold: str) -> RuleResult:
    distance = (
        "meets threshold"
        if state is TriState.PASS
        else "evidence disqualifies"
        if state is TriState.FAIL
        else "distance unknown"
    )
    return RuleResult(name, state, state.value, threshold, distance)


def _boolean_rule(
    name: str, value: bool | None, threshold: str, *, actual: str | None = None
) -> RuleResult:
    state = (
        TriState.UNKNOWN if value is None else TriState.PASS if value else TriState.FAIL
    )
    actual_value = actual or ("unknown" if value is None else str(value).lower())
    distance = (
        "distance unknown"
        if value is None
        else "meets threshold"
        if value
        else f"actual {actual_value} does not meet requirement"
    )
    return RuleResult(name, state, actual_value, threshold, distance)


def _money_max_rule(name: str, actual: int | None, maximum: int) -> RuleResult:
    threshold = f"at most {_pln(maximum)}"
    if actual is None:
        return RuleResult(
            name, TriState.UNKNOWN, "unknown", threshold, "distance unknown"
        )
    state = TriState.PASS if actual <= maximum else TriState.FAIL
    difference = maximum - actual
    distance = (
        f"under by {_pln(difference)}"
        if difference >= 0
        else f"over by {_pln(-difference)}"
    )
    return RuleResult(name, state, _pln(actual), threshold, distance)


def _decimal_min_rule(
    name: str, actual: Decimal | None, minimum: Decimal, unit: str
) -> RuleResult:
    threshold = f"at least {minimum} {unit}"
    if actual is None:
        return RuleResult(
            name, TriState.UNKNOWN, "unknown", threshold, "distance unknown"
        )
    difference = actual - minimum
    state = TriState.PASS if difference >= 0 else TriState.FAIL
    direction = "above" if difference >= 0 else "below"
    return RuleResult(
        name,
        state,
        f"{actual} {unit}",
        threshold,
        f"{direction} by {abs(difference)} {unit}",
    )


def _integer_min_rule(
    name: str, actual: int | None, minimum: int, unit: str
) -> RuleResult:
    threshold = f"at least {minimum} {unit}{'' if minimum == 1 else 's'}"
    if actual is None:
        return RuleResult(
            name, TriState.UNKNOWN, "unknown", threshold, "distance unknown"
        )
    difference = actual - minimum
    state = TriState.PASS if difference >= 0 else TriState.FAIL
    direction = "above" if difference >= 0 else "below"
    amount = abs(difference)
    suffix = "" if amount == 1 else "s"
    return RuleResult(
        name, state, str(actual), threshold, f"{direction} by {amount} {unit}{suffix}"
    )


def _integer_max_rule(
    name: str, actual: int | None, maximum: int, unit: str
) -> RuleResult:
    threshold = f"at most {maximum} {unit}{'' if maximum == 1 else 's'}"
    if actual is None:
        return RuleResult(
            name, TriState.UNKNOWN, "unknown", threshold, "distance unknown"
        )
    difference = maximum - actual
    state = TriState.PASS if difference >= 0 else TriState.FAIL
    direction = "under" if difference >= 0 else "over"
    amount = abs(difference)
    suffix = "" if amount == 1 else "s"
    return RuleResult(
        name, state, str(actual), threshold, f"{direction} by {amount} {unit}{suffix}"
    )


def _floor_rule(facts: PropertyFacts) -> RuleResult:
    threshold = "below building top floor; ground floor requires private garden"
    if facts.floor is None or facts.building_top_floor is None:
        floor = facts.floor if facts.floor is not None else "unknown"
        actual = f"floor {floor}, top unknown"
        return RuleResult(
            "floor", TriState.UNKNOWN, actual, threshold, "distance unknown"
        )
    actual = f"floor {facts.floor}, top {facts.building_top_floor}"
    if facts.floor >= facts.building_top_floor:
        return RuleResult("floor", TriState.FAIL, actual, threshold, "top floor")
    if facts.floor == 0:
        if facts.has_private_garden is None:
            return RuleResult(
                "floor", TriState.UNKNOWN, actual, threshold, "garden unknown"
            )
        if not facts.has_private_garden:
            return RuleResult(
                "floor", TriState.FAIL, actual, threshold, "private garden missing"
            )
    return RuleResult(
        "floor",
        TriState.PASS,
        actual,
        threshold,
        f"{facts.building_top_floor - facts.floor} floor(s) below top",
    )


def _elevator_rule(facts: PropertyFacts) -> RuleResult:
    threshold = "required above floor 3"
    if facts.floor is None:
        return RuleResult(
            "elevator", TriState.UNKNOWN, "unknown", threshold, "distance unknown"
        )
    if facts.floor <= 3:
        return RuleResult(
            "elevator", TriState.PASS, f"floor {facts.floor}", threshold, "not required"
        )
    return _boolean_rule(
        "elevator",
        facts.has_elevator,
        threshold,
        actual=(
            "unknown" if facts.has_elevator is None else str(facts.has_elevator).lower()
        ),
    )


def _primary_market_evidence(facts: PropertyFacts) -> TriState:
    if facts.market_type is None:
        return TriState.UNKNOWN
    if facts.market_type != MarketType.PRIMARY:
        return TriState.PASS
    return facts.primary_market_eligibility or TriState.UNKNOWN


def _pln(value_minor: int) -> str:
    return f"PLN {Decimal(value_minor) / Decimal(100):,.2f}"


def _score_components(
    facts: PropertyFacts, profile: BuyerProfile
) -> tuple[ScoreComponent, ...]:
    weights = profile.weights()
    values: dict[str, tuple[Decimal, str]] = {}
    for name in (
        "ready_to_move",
        "quiet",
        "green_space",
        "balcony",
        "separate_kitchen",
    ):
        value = getattr(facts, name)
        values[name] = (
            Decimal("1")
            if value is True
            else Decimal("0")
            if value is False
            else Decimal("0.5"),
            f"{name.replace('_', ' ')} evidence",
        )
    if facts.building_dwellings is None:
        values["small_building"] = (Decimal("0.5"), "building size unknown")
    elif facts.building_dwellings <= 20:
        values["small_building"] = (Decimal("1"), "up to 20 dwellings")
    elif facts.building_dwellings <= 40:
        values["small_building"] = (Decimal("0.75"), "21–40 dwellings")
    elif facts.building_dwellings <= 80:
        values["small_building"] = (Decimal("0.25"), "41–80 dwellings")
    else:
        values["small_building"] = (Decimal("0"), "more than 80 dwellings")
    if facts.area_sqm is None:
        values["area_fit"] = (Decimal("0.5"), "area unknown")
    elif profile.ideal_area_low_sqm <= facts.area_sqm <= profile.ideal_area_high_sqm:
        values["area_fit"] = (Decimal("1"), "within preferred 48–55 m²")
    else:
        distance = min(
            abs(facts.area_sqm - profile.ideal_area_low_sqm),
            abs(facts.area_sqm - profile.ideal_area_high_sqm),
        )
        values["area_fit"] = (
            max(Decimal("0"), Decimal("1") - distance / Decimal("20")),
            "outside preferred band",
        )
    return tuple(
        ScoreComponent(name, value, weights[name], reason)
        for name, (value, reason) in values.items()
    )
