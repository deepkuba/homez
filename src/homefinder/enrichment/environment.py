"""Conservative, evidence-backed environmental and building facts."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from homefinder.domain.matching import PropertyFacts


class NoiseRisk(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class Evidence:
    """A derived value's provenance; values are never evidence-free."""

    source: str
    observed_at: datetime
    confidence: Decimal
    note: str = ""

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("evidence source is required")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("evidence confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class RoadIndicator:
    name: str
    distance_m: int | None
    road_class: str | None = None
    evidence: Evidence | None = None


@dataclass(frozen=True, slots=True)
class EnvironmentalFacts:
    property_id: str
    address_confidence: Decimal | None = None
    noise_risk: NoiseRisk = NoiseRisk.UNKNOWN
    green_space_distance_m: int | None = None
    floor: int | None = None
    has_elevator: bool | None = None
    entrance_dwellings: int | None = None
    building_dwellings: int | None = None
    evidence: dict[str, Evidence] | None = None
    road_indicators: tuple[RoadIndicator, ...] = ()

    def __post_init__(self) -> None:
        if self.address_confidence is not None and not (
            Decimal("0") <= self.address_confidence <= Decimal("1")
        ):
            raise ValueError("address confidence must be between 0 and 1")
        for value in (
            self.green_space_distance_m,
            self.floor,
            self.entrance_dwellings,
            self.building_dwellings,
        ):
            if value is not None and value < 0:
                raise ValueError("environment measurements cannot be negative")

    @property
    def green_space(self) -> bool | None:
        if self.green_space_distance_m is None:
            return None
        return self.green_space_distance_m <= 800

    @property
    def quiet(self) -> bool | None:
        return {
            NoiseRisk.LOW: True,
            NoiseRisk.MODERATE: None,
            NoiseRisk.HIGH: False,
        }.get(self.noise_risk)

    def apply_to(self, facts: PropertyFacts) -> PropertyFacts:
        """Map known enrichment to matching facts without inventing missing data."""
        if facts.id != self.property_id:
            raise ValueError("environment facts belong to a different property")
        updates: dict[str, Any] = {}
        for name in (
            "floor",
            "has_elevator",
            "entrance_dwellings",
            "building_dwellings",
        ):
            value = getattr(self, name)
            if value is not None:
                updates[name] = value
        if self.quiet is not None:
            updates["quiet"] = self.quiet
        if self.green_space is not None:
            updates["green_space"] = self.green_space
        return replace(facts, **updates)


@dataclass(frozen=True, slots=True)
class ManualCorrection:
    property_id: str
    field: str
    value: str
    corrected_at: datetime
    corrected_by: str
    reason: str


class ManualCorrectionStore:
    """Auditable fixture store; production uses the matching migration tables."""

    allowed_fields = frozenset(
        {
            "address_confidence",
            "green_space_distance_m",
            "floor",
            "has_elevator",
            "entrance_dwellings",
            "building_dwellings",
            "noise_risk",
        }
    )

    def __init__(self) -> None:
        self.corrections: list[ManualCorrection] = []

    def record(
        self,
        *,
        property_id: str,
        field: str,
        value: str,
        corrected_by: str,
        reason: str,
        corrected_at: datetime,
    ) -> ManualCorrection:
        if field not in self.allowed_fields:
            raise ValueError("field is not correctable")
        if not corrected_by.strip() or not reason.strip():
            raise ValueError("corrector and reason are required")
        correction = ManualCorrection(
            property_id, field, value, corrected_at, corrected_by, reason
        )
        self.corrections.append(correction)
        return correction


class EnvironmentalEnricher:
    """Small deterministic heuristic; adapters provide the open-data inputs."""

    def __init__(self, *, freshness: timedelta = timedelta(days=30)) -> None:
        self.freshness = freshness

    def derive(
        self,
        *,
        property_id: str,
        observed_at: datetime,
        address_confidence: Decimal | None = None,
        road_indicators: tuple[RoadIndicator, ...] = (),
        green_space_distance_m: int | None = None,
        floor: int | None = None,
        has_elevator: bool | None = None,
        entrance_dwellings: int | None = None,
        building_dwellings: int | None = None,
    ) -> EnvironmentalFacts:
        evidence: dict[str, Evidence] = {}
        for field, value in {
            "address_confidence": address_confidence,
            "green_space_distance_m": green_space_distance_m,
            "floor": floor,
            "has_elevator": has_elevator,
            "entrance_dwellings": entrance_dwellings,
            "building_dwellings": building_dwellings,
        }.items():
            if value is not None:
                evidence[field] = Evidence(
                    "open-data-or-listing", observed_at, Decimal("0.70")
                )
        for indicator in road_indicators:
            if indicator.evidence is not None:
                evidence[f"road:{indicator.name}"] = indicator.evidence
        noise = self._noise(road_indicators)
        if road_indicators:
            evidence["noise_risk"] = Evidence(
                "road-indicators", observed_at, Decimal("0.55")
            )
        return EnvironmentalFacts(
            property_id,
            address_confidence,
            noise,
            green_space_distance_m,
            floor,
            has_elevator,
            entrance_dwellings,
            building_dwellings,
            evidence,
            road_indicators,
        )

    def is_fresh(self, evidence: Evidence, *, now: datetime) -> bool:
        return now - evidence.observed_at <= self.freshness

    @staticmethod
    def _noise(indicators: tuple[RoadIndicator, ...]) -> NoiseRisk:
        if not indicators or all(i.distance_m is None for i in indicators):
            return NoiseRisk.UNKNOWN
        distances = [i.distance_m for i in indicators if i.distance_m is not None]
        if any(distance <= 50 for distance in distances):
            return NoiseRisk.HIGH
        if any(distance <= 150 for distance in distances):
            return NoiseRisk.MODERATE
        return NoiseRisk.LOW
