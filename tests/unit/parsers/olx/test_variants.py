from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from homefinder.benchmark import BenchmarkEntryResult, eligible_for_activation
from homefinder.parsers.contracts import PageInput
from homefinder.parsers.olx import OlxPageParser

FIXTURES = Path("tests/fixtures/parsers/olx")


def test_reviewed_olx_fixture_extracts_core_contract() -> None:
    page = PageInput(
        UUID("00000000-0000-4000-8000-000000000015"),
        datetime(2026, 9, 10, tzinfo=timezone.utc),
        (FIXTURES / "listing-v2.html").read_bytes(),
    )

    result = OlxPageParser("d" * 64).parse(page)

    assert result.variant == "listing-v2"
    assert result.facts.title == "Synthetic OLX studio"
    assert result.facts.price_minor == 36_000_000
    assert str(result.facts.area_sqm) == "30"
    assert result.facts.rooms == 1
    assert result.facts.location == "Example Borough"
    assert result.facts.heating_type == "electric"


def test_synthetic_olx_fixture_cannot_qualify_without_raw_coverage() -> None:
    page = PageInput(
        UUID("00000000-0000-4000-8000-000000000115"),
        datetime(2026, 9, 10, tzinfo=timezone.utc),
        (FIXTURES / "listing-v2.html").read_bytes(),
    )
    parsed = OlxPageParser("d" * 64).parse(page)

    assert not eligible_for_activation(
        results=(
            BenchmarkEntryResult(
                "olx-listing-v2-synthetic",
                "fixture",
                parsed.variant,
                "compared",
                parsed,
                parsed,
                None,
            ),
        ),
        reviews=(),
        changed_variants=frozenset({parsed.variant}),
        evaluated_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
