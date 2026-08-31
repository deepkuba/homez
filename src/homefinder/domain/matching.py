"""Tri-state eligibility, confidence, and explainable match scoring."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from homefinder.domain.profile import BuyerProfile


class TriState(str, Enum):
    PASS = "pass"  # noqa: S105 - a tri-state label, not a credential
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PropertyFacts:
    id: str
    title: str = ""
    locality: str | None = None
    price_minor: int | None = None
    area_sqm: Decimal | None = None
    rooms: int | None = None
    purchase_type: str | None = "purchase"
    primary_market_eligibility: TriState | None = None
    vacant_possession: bool | None = None
    separate_ownership: bool | None = None
    serious_legal_risk: bool | None = None
    floor: int | None = None
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


@dataclass(frozen=True, slots=True)
class RuleResult:
    name: str
    state: TriState
    explanation: str


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
    rules = (
        _rule(
            "purchase",
            facts.purchase_type == "purchase"
            if facts.purchase_type is not None
            else None,
            "purchase only",
        ),
        _rule(
            "primary_market_evidence",
            _primary_market_evidence(facts),
            "critical primary-market evidence is sufficient",
        ),
        _rule("vacant_possession", facts.vacant_possession, "delivered vacant"),
        _rule(
            "separate_ownership",
            facts.separate_ownership,
            "separate ownership required",
        ),
        _rule(
            "legal_risk",
            None if facts.serious_legal_risk is None else not facts.serious_legal_risk,
            "no known serious legal risk",
        ),
        _rule(
            "locality",
            None
            if facts.locality is None
            else facts.locality.casefold() not in profile.excluded_localities,
            "locality is not excluded",
        ),
        _rule(
            "price",
            None
            if facts.price_minor is None
            else facts.price_minor <= profile.max_purchase_price_minor,
            f"at or below PLN {profile.max_purchase_price_minor / 100:,.0f}",
        ),
        _rule(
            "area",
            None if facts.area_sqm is None else facts.area_sqm >= profile.min_area_sqm,
            f"at least {profile.min_area_sqm} m²",
        ),
        _rule(
            "rooms",
            None if facts.rooms is None else facts.rooms >= profile.min_rooms,
            "at least two rooms",
        ),
        _rule(
            "usable_layout", facts.usable_layout, "living room and separate usable room"
        ),
        _rule(
            "floor",
            _floor_state(facts),
            "not top floor; ground floor requires private garden",
        ),
        _rule(
            "elevator",
            None
            if facts.floor is None
            else True
            if facts.floor <= 3
            else facts.has_elevator,
            "elevator above third floor",
        ),
        _rule("parking", facts.parking_possible, "parking possible where required"),
        _rule(
            "building_scale",
            None
            if facts.building_dwellings is None
            else facts.building_dwellings <= profile.max_building_dwellings,
            "no more than 80 dwellings",
        ),
        _rule(
            "commute",
            None
            if facts.commute_minutes is None
            else facts.commute_minutes <= profile.max_commute_minutes,
            "commute within 45 minutes",
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
        f"{r.name}: {r.explanation} ({r.state.value})"
        for r in rules
        if r.state is not TriState.PASS
    )
    return MatchExplanation(
        rules,
        components,
        score.quantize(Decimal("0.01")),
        confidence.quantize(Decimal("0.01")),
        reasons,
    )


def _rule(name: str, value: bool | None, requirement: str) -> RuleResult:
    state = (
        TriState.UNKNOWN if value is None else TriState.PASS if value else TriState.FAIL
    )
    return RuleResult(name, state, requirement)


def _floor_state(facts: PropertyFacts) -> bool | None:
    if facts.floor is None:
        return None
    if facts.floor == 0:
        return facts.has_private_garden
    return True


def _primary_market_evidence(facts: PropertyFacts) -> bool | None:
    if facts.purchase_type != "primary":
        return True
    if facts.primary_market_eligibility is None:
        return None
    if facts.primary_market_eligibility is TriState.UNKNOWN:
        return None
    return facts.primary_market_eligibility is TriState.PASS


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
