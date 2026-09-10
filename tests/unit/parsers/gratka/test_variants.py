from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from homefinder.benchmark import BenchmarkEntryResult, eligible_for_activation
from homefinder.parsers.contracts import FieldState, PageInput
from homefinder.parsers.gratka import GratkaPageParser

FIXTURES = Path("tests/fixtures/parsers/gratka")


def test_reviewed_gratka_fixture_extracts_core_contract() -> None:
    page = PageInput(
        UUID("00000000-0000-4000-8000-000000000017"),
        datetime(2026, 9, 10, tzinfo=timezone.utc),
        (FIXTURES / "graph.html").read_bytes(),
    )

    result = GratkaPageParser("a" * 64).parse(page)

    assert result.variant == "jsonld-graph-residence-v2"
    assert result.facts.title == "Synthetic graph apartment"
    assert result.facts.price_minor == 81_000_000
    assert result.facts.currency == "PLN"
    assert str(result.facts.area_sqm) == "54.0"
    assert result.facts.rooms == 3
    assert result.facts.location == "Example Town"
    assert result.facts.availability == "active"
    assert (
        next(field for field in result.fields if field.name == "title").state
        is FieldState.VALUE
    )


def test_synthetic_gratka_fixture_cannot_qualify_release_without_raw_coverage() -> None:
    page = PageInput(
        UUID("00000000-0000-4000-8000-000000000018"),
        datetime(2026, 9, 10, tzinfo=timezone.utc),
        (FIXTURES / "graph.html").read_bytes(),
    )
    parsed = GratkaPageParser("b" * 64).parse(page)

    assert not eligible_for_activation(
        results=(
            BenchmarkEntryResult(
                "gratka-graph-synthetic",
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
