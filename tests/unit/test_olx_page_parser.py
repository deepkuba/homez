from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from homefinder.parsers.contracts import PageInput
from homefinder.parsers.olx.parser import OlxPageParser


def test_olx_positive_variant_extracts_baseline_independently():
    page = PageInput(
        uuid4(),
        datetime(2026, 9, 10, tzinfo=timezone.utc),
        Path("tests/fixtures/parsers/olx/baseline.html").read_bytes(),
    )
    result = OlxPageParser("b" * 64).parse(page)
    assert result.variant == "listing-v1"
    assert result.facts.title == "Synthetic OLX flat"
    assert result.facts.price_minor == 42_000_000
    assert result.facts.price_per_sqm_minor == 1_200_000
    assert result.missing_fields == ()


def test_olx_zero_or_multiple_variants_fail_unknown():
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    for body in (
        b"<html><body>synthetic</body></html>",
        b'<meta name="olx:variant" content="listing-v1">' * 2,
    ):
        result = OlxPageParser("b" * 64).parse(PageInput(uuid4(), now, body))
        assert result.variant == "unknown-variant"
        assert len(result.missing_fields) == 11
        assert result.candidates == ()
