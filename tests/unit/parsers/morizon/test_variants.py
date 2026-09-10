from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from homefinder.benchmark import BenchmarkEntryResult, eligible_for_activation
from homefinder.parsers.contracts import PageInput
from homefinder.parsers.morizon import MorizonPageParser

FIXTURES = Path("tests/fixtures/parsers/morizon")


def test_reviewed_morizon_fixture_extracts_core_contract() -> None:
    page = PageInput(
        UUID("00000000-0000-4000-8000-000000000013"),
        datetime(2026, 9, 10, tzinfo=timezone.utc),
        (FIXTURES / "main-entity.html").read_bytes(),
    )

    result = MorizonPageParser("b" * 64).parse(page)

    assert result.variant == "jsonld-main-entity-residence-v2"
    assert result.facts.title == "Synthetic detached house"
    assert result.facts.price_minor == 125_000_000
    assert str(result.facts.area_sqm) == "112.5"
    assert result.facts.rooms == 5
    assert result.facts.location == "Example Village"


def test_synthetic_morizon_fixture_cannot_qualify_without_raw_coverage() -> None:
    page = PageInput(
        UUID("00000000-0000-4000-8000-000000000113"),
        datetime(2026, 9, 10, tzinfo=timezone.utc),
        (FIXTURES / "main-entity.html").read_bytes(),
    )
    parsed = MorizonPageParser("b" * 64).parse(page)

    assert not eligible_for_activation(
        results=(
            BenchmarkEntryResult(
                "morizon-main-entity-synthetic",
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
