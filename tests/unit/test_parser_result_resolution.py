import pytest


def test_price_per_sqm_prefers_page_value_with_100_pln_tolerance():
    from homefinder.parsers.results import resolve_price_per_sqm

    assert resolve_price_per_sqm(50_000_000, "40", explicit_minor=1_251_000) == (
        1_251_000,
        False,
    )
    assert resolve_price_per_sqm(50_000_000, "40", explicit_minor=1_260_100) == (
        1_260_100,
        True,
    )
    assert resolve_price_per_sqm(50_000_000, "40", explicit_minor=None) == (
        1_250_000,
        False,
    )


def test_price_per_sqm_levels_validate_ordered_configurable_boundaries():
    from homefinder.parsers.results import PricePerSqmBands

    bands = PricePerSqmBands(
        low_upper_bound_minor=1_200_000,
        high_lower_bound_minor=1_800_000,
        currency="PLN",
    )
    assert bands.classify(1_200_000) == "low"
    assert bands.classify(1_500_000) == "normal"
    assert bands.classify(1_800_000) == "high"
    assert bands.classify(None) == "unknown"
    with pytest.raises(ValueError):
        PricePerSqmBands(1_800_000, 1_800_000, "PLN")
