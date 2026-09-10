from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from homefinder.benchmark import BenchmarkEntryResult, eligible_for_activation
from homefinder.parsers.contracts import PageInput
from homefinder.parsers.otodom import OtodomPageParser

FIXTURES = Path("tests/fixtures/parsers/otodom")


def test_reviewed_otodom_fixture_extracts_core_contract() -> None:
    page = PageInput(
        UUID("00000000-0000-4000-8000-000000000014"),
        datetime(2026, 9, 10, tzinfo=timezone.utc),
        (FIXTURES / "listing-v2.html").read_bytes(),
    )

    result = OtodomPageParser("c" * 64).parse(page)

    assert result.variant == "listing-v2"
    assert result.facts.title == "Synthetic Otodom house"
    assert result.facts.price_minor == 92_000_000
    assert str(result.facts.area_sqm) == "80"
    assert result.facts.rooms == 4
    assert result.facts.location == "Example District"
    assert result.facts.heating_type == "gas"


def test_synthetic_otodom_fixture_cannot_qualify_without_raw_coverage() -> None:
    page = PageInput(
        UUID("00000000-0000-4000-8000-000000000114"),
        datetime(2026, 9, 10, tzinfo=timezone.utc),
        (FIXTURES / "listing-v2.html").read_bytes(),
    )
    parsed = OtodomPageParser("c" * 64).parse(page)

    assert not eligible_for_activation(
        results=(
            BenchmarkEntryResult(
                "otodom-listing-v2-synthetic",
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
