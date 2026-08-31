from pathlib import Path

import pytest

from homefinder.sources.errors import AlertParseError
from homefinder.sources.portal_alerts import (
    OLX_BLOCKED_REASON,
    GratkaAlertParser,
    MorizonAlertParser,
    OtodomAlertParser,
)

FIXTURES = Path(__file__).parents[2] / "data" / "email_examples"

PARSERS = (
    ("otodom", OtodomAlertParser),
    ("morizon", MorizonAlertParser),
    ("gratka", GratkaAlertParser),
)


@pytest.mark.parametrize(("source_key", "parser_type"), PARSERS)
def test_approved_portal_contract_parses_sanitized_fixture(
    source_key: str, parser_type: type[OtodomAlertParser]
) -> None:
    parsed = parser_type().parse((FIXTURES / f"{source_key}_alert.eml").read_bytes())

    assert parsed.source.key == source_key
    assert parsed.message.parser_version == "sanitized-email-v1"
    assert len(parsed.items) == 1
    assert parsed.listing.source_listing_id == f"{source_key}-example-001"
    assert parsed.snapshot.currency == "PLN"


@pytest.mark.parametrize(("source_key", "parser_type"), PARSERS)
def test_approved_portal_contract_rejects_format_drift(
    source_key: str, parser_type: type[OtodomAlertParser]
) -> None:
    raw = (
        (FIXTURES / f"{source_key}_alert.eml")
        .read_bytes()
        .replace(b'data-field="price"', b'data-field="asking-price"')
    )

    with pytest.raises(AlertParseError, match="required-fields"):
        parser_type().parse(raw)


@pytest.mark.parametrize(("source_key", "parser_type"), PARSERS)
def test_approved_portal_contract_rejects_wrong_sender_and_source(
    source_key: str, parser_type: type[OtodomAlertParser]
) -> None:
    raw = (FIXTURES / f"{source_key}_alert.eml").read_bytes()

    with pytest.raises(AlertParseError, match="unexpected-sender"):
        parser_type().parse(raw.replace(b"alerts@example.com", b"bad@example.com"))
    with pytest.raises(AlertParseError, match="source-identity"):
        parser_type().parse(
            raw.replace(
                f"X-Homez-Source: {source_key}".encode(),
                b"X-Homez-Source: another",
            )
        )


@pytest.mark.parametrize(("source_key", "parser_type"), PARSERS)
def test_approved_portal_contract_rejects_non_contract_listing_url(
    source_key: str, parser_type: type[OtodomAlertParser]
) -> None:
    raw = (
        (FIXTURES / f"{source_key}_alert.eml")
        .read_bytes()
        .replace(
            b"https://example.com/listings/", b"https://attacker.example/listings/"
        )
    )

    with pytest.raises(AlertParseError, match="listing-url"):
        parser_type().parse(raw)


def test_portal_contract_normalizes_multiple_listings() -> None:
    raw = (FIXTURES / "morizon_alert.eml").read_bytes()
    second = b"""
    <article data-listing-id="morizon-example-002">
      <h2 data-field="title">Example second apartment</h2>
      <a data-field="url" href="https://example.com/listings/morizon-example-002">View</a>
      <span data-field="price">900000 PLN</span>
      <span data-field="area">70.5 m2</span>
      <span data-field="rooms">4</span>
      <span data-field="location">Another Example District</span>
    </article>
    """
    raw = raw.replace(b"</body>", second + b"</body>")

    parsed = MorizonAlertParser().parse(raw)

    assert [item.listing.source_listing_id for item in parsed.items] == [
        "morizon-example-001",
        "morizon-example-002",
    ]
    assert parsed.items[1].snapshot.price_minor == 90_000_000


def test_portal_contract_rejects_oversized_and_invalid_numeric_messages() -> None:
    raw = (FIXTURES / "gratka_alert.eml").read_bytes()
    with pytest.raises(AlertParseError, match="message-size"):
        GratkaAlertParser().parse(raw + b" " * 512_000)
    with pytest.raises(AlertParseError, match="numeric-values"):
        GratkaAlertParser().parse(raw.replace(b"700000 PLN", b"free PLN"))


def test_olx_contract_remains_explicitly_blocked() -> None:
    assert OLX_BLOCKED_REASON == (
        "fixture unavailable; blocked on human issues #17 and #18"
    )
