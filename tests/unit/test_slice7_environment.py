from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from homefinder.domain.matching import PropertyFacts
from homefinder.enrichment.environment import (
    EnvironmentalEnricher,
    ManualCorrectionStore,
    NoiseRisk,
    RoadIndicator,
)

AT = datetime(2026, 8, 31, tzinfo=timezone.utc)


def test_environment_derives_conservative_noise_green_and_building_facts() -> None:
    result = EnvironmentalEnricher().derive(
        property_id="p1",
        observed_at=AT,
        address_confidence=Decimal(".9"),
        road_indicators=(RoadIndicator("arterial", 40),),
        green_space_distance_m=600,
        floor=4,
        has_elevator=True,
        entrance_dwellings=18,
        building_dwellings=36,
    )
    assert result.noise_risk is NoiseRisk.HIGH
    assert result.green_space is True
    assert result.quiet is False
    assert result.evidence["noise_risk"].source == "road-indicators"
    assert result.apply_to(PropertyFacts("p1")).has_elevator is True


def test_missing_environment_evidence_stays_unknown_and_never_rejects() -> None:
    result = EnvironmentalEnricher().derive(property_id="p1", observed_at=AT)
    mapped = result.apply_to(PropertyFacts("p1"))
    assert result.noise_risk is NoiseRisk.UNKNOWN
    assert result.green_space is None
    assert mapped.quiet is None
    assert mapped.green_space is None


def test_evidence_freshness_is_explicit() -> None:
    enricher = EnvironmentalEnricher(freshness=timedelta(days=7))
    facts = enricher.derive(property_id="p1", observed_at=AT, floor=2)
    evidence = facts.evidence["floor"]
    assert enricher.is_fresh(evidence, now=AT + timedelta(days=7))
    assert not enricher.is_fresh(evidence, now=AT + timedelta(days=7, seconds=1))


def test_negative_measurements_are_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        EnvironmentalEnricher().derive(property_id="p1", observed_at=AT, floor=-1)


def test_manual_correction_is_scoped_and_auditable() -> None:
    store = ManualCorrectionStore()
    correction = store.record(
        property_id="p1",
        field="has_elevator",
        value="true",
        corrected_by="buyer",
        reason="confirmed during viewing",
        corrected_at=AT,
    )
    assert correction.property_id == "p1"
    with pytest.raises(ValueError, match="not correctable"):
        store.record(
            property_id="p1",
            field="price_minor",
            value="1",
            corrected_by="buyer",
            reason="x",
            corrected_at=AT,
        )
