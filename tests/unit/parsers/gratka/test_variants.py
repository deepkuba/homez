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


def test_reviewed_gratka_nuxt_fixture_extracts_page_contract() -> None:
    page = PageInput(
        UUID("00000000-0000-4000-8000-000000000019"),
        datetime(2026, 9, 23, tzinfo=timezone.utc),
        (FIXTURES / "nuxt-property.html").read_bytes(),
    )

    result = GratkaPageParser("c" * 64).parse(page)

    assert result.variant == "nuxt-property-v1"
    assert result.facts.title == "Synthetic Nuxt apartment"
    assert result.facts.price_minor == 81_000_000
    assert result.facts.currency == "PLN"
    assert result.facts.area_sqm == "54.0"
    assert result.facts.rooms == 3
    assert result.facts.location == "Example Town"
    assert result.facts.description == "Synthetic apartment description"
    assert result.facts.monthly_admin_fee_minor == 65_000
    assert result.facts.heating_type == "district"
    assert result.facts.price_per_sqm_minor == 1_500_000
    assert result.facts.availability == "unknown"
    assert result.facts.admin_fee_includes_heating is None


def test_gratka_nuxt_detector_fails_closed_on_multiple_property_payloads() -> None:
    body = (
        b'<script type="application/json" id="__NUXT_DATA__">'
        b'[{"propertyData":1,"other":2},{"title":3,"price":4,"area":7,'
        b'"numberOfRooms":8,"description":9},{"propertyData":10},'
        b'"First",{"amount":5,"currency":6},100000,"PLN","50","2","One",'
        b'{"title":11,"price":12,"area":15,"numberOfRooms":16,'
        b'"description":17},"Second",{"amount":13,"currency":14},200000,'
        b'"PLN","60","3","Two"]</script>'
    )
    page = PageInput(
        UUID("00000000-0000-4000-8000-000000000020"),
        datetime(2026, 9, 23, tzinfo=timezone.utc),
        body,
    )

    assert GratkaPageParser("d" * 64).parse(page).variant == "unknown-variant"
