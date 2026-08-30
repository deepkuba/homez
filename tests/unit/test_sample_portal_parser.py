from pathlib import Path

import pytest

from homefinder.sources.errors import AlertParseError
from homefinder.sources.sample_portal import SamplePortalAlertParser

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sample_portal"


def test_parser_extracts_sanitized_alert() -> None:
    parsed = SamplePortalAlertParser().parse(
        (FIXTURES / "valid_alert.eml").read_bytes()
    )

    assert parsed.message.provider_message_id == (
        "sample-20260830-001@fixtures.homez.invalid"
    )
    assert parsed.listing.source_listing_id == "sample-krk-001"
    assert parsed.listing.title == "Jasne 2 pokoje z balkonem"
    assert parsed.snapshot.price_minor == 74_900_000
    assert str(parsed.snapshot.area_sqm) == "51.4"
    assert parsed.snapshot.rooms == 2
    assert parsed.snapshot.availability == "available"


def test_parser_rejects_missing_required_listing_fields() -> None:
    with pytest.raises(AlertParseError, match="missing required fields"):
        SamplePortalAlertParser().parse((FIXTURES / "malformed_alert.eml").read_bytes())


def test_parser_rejects_unsupported_sender() -> None:
    raw = (
        (FIXTURES / "valid_alert.eml")
        .read_bytes()
        .replace(b"alerts@fixtures.homez.invalid", b"attacker@example.invalid")
    )

    with pytest.raises(AlertParseError, match="unexpected sender"):
        SamplePortalAlertParser().parse(raw)
