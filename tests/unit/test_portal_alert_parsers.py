import base64
from pathlib import Path

import pytest

from homefinder.cli import _gmail_pollers, _portal_parser
from homefinder.config import Settings
from homefinder.sources.errors import AlertParseError
from homefinder.sources.policy import SourcePolicy
from homefinder.sources.portal_alerts import (
    GratkaAlertParser,
    MorizonAlertParser,
    OLXAlertParser,
    OtodomAlertParser,
)

FIXTURES = Path(__file__).parents[2] / "data" / "email_examples"

PARSERS = (
    ("otodom", OtodomAlertParser),
    ("morizon", MorizonAlertParser),
    ("gratka", GratkaAlertParser),
    ("olx", OLXAlertParser),
)


def _tracking_link_message(source_key: str, href: str) -> bytes:
    return f"""Message-ID: <{source_key}-tracking@example.com>
Date: Mon, 31 Aug 2026 15:00:00 +0200
From: Portal alerts <alerts@example.com>
Subject: Tracking link example
Content-Type: text/html; charset=utf-8

<html><body>
  <article data-listing-id="{source_key}-tracking-001">
    <h2 data-field="title">Example tracked apartment</h2>
    <a data-field="url" href="{href}">View</a>
    <span data-field="price">750000 PLN</span>
    <span data-field="area">55.5 m2</span>
    <span data-field="rooms">3</span>
    <span data-field="location">Example North District</span>
  </article>
</body></html>""".encode()


def _base64url(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


@pytest.mark.parametrize(("source_key", "parser_type"), PARSERS)
def test_approved_portal_contract_parses_sanitized_fixture(
    source_key: str, parser_type: type[OtodomAlertParser]
) -> None:
    parsed = parser_type().parse((FIXTURES / f"{source_key}_alert.eml").read_bytes())

    assert parsed.source.key == source_key
    assert parsed.message.parser_version == "portal-email-v3"
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
def test_approved_portal_contract_uses_sender_allowlist_for_source_identity(
    source_key: str, parser_type: type[OtodomAlertParser]
) -> None:
    raw = (FIXTURES / f"{source_key}_alert.eml").read_bytes()

    with pytest.raises(AlertParseError, match="unexpected-sender"):
        parser_type().parse(raw.replace(b"alerts@example.com", b"bad@example.com"))

    with_forged_private_identity_header = raw.replace(
        b"MIME-Version: 1.0",
        b"X-Homez-Source: another\nMIME-Version: 1.0",
    )
    assert (
        parser_type().parse(with_forged_private_identity_header).source.key
        == source_key
    )


def test_portal_contract_accepts_all_configured_senders() -> None:
    raw = (
        (FIXTURES / "otodom_alert.eml")
        .read_bytes()
        .replace(b"alerts@example.com", b"second@example.com")
    )
    parser = OtodomAlertParser(
        allowed_senders=frozenset({"alerts@example.com", "second@example.com"})
    )

    assert parser.parse(raw).message.sender == "second@example.com"


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


@pytest.mark.parametrize(
    "parser_type",
    [OtodomAlertParser, MorizonAlertParser, GratkaAlertParser, OLXAlertParser],
)
def test_realistic_table_card_is_parsed_without_contract_attributes(
    parser_type: type[OtodomAlertParser],
) -> None:
    source_key = parser_type.source_key
    raw = f"""Message-ID: <{source_key}-generic@example.com>
Date: Mon, 31 Aug 2026 15:00:00 +0200
From: Portal alerts <alerts@example.com>
To: email@example.com
Subject: New matching listings
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8

<html><body><table role="presentation">
  <tr><td class="listing-card">
    <h2><a
      href="https://example.com/listings/{source_key}-generic-001?utm_source=email"
    >Example garden apartment</a></h2>
    <p class="location">Example North District</p>
    <p>750 000 zł</p><p>55,5 m² | 3 pokoje</p>
  </td></tr>
  <tr><td class="listing-card">
    <h2><a href="https://example.com/listings/{source_key}-generic-002"
    >Example city apartment</a></h2>
    <p class="address">Example South District</p>
    <p>640000 PLN</p><p>44 m2 | 2 rooms</p>
  </td></tr>
</table></body></html>""".encode()

    parsed = parser_type().parse(raw)

    assert len(parsed.items) == 2
    assert parsed.items[0].listing.canonical_url == (
        f"https://example.com/listings/{source_key}-generic-001"
    )
    assert parsed.items[0].snapshot.location == "Example North District"
    assert parsed.items[0].snapshot.price_minor == 75_000_000
    assert parsed.items[0].snapshot.area_sqm == 55.5
    assert parsed.items[0].snapshot.rooms == 3


def test_contract_extraction_rejects_unallowlisted_listing_links() -> None:
    raw = (
        (FIXTURES / "olx_alert.eml")
        .read_bytes()
        .replace(
            b"https://example.com/listings/olx-example-001",
            b"https://attacker.example/listings/olx-example-001",
        )
    )

    with pytest.raises(AlertParseError, match="listing-url"):
        OLXAlertParser().parse(raw)


def test_generic_parser_rejects_excessive_html_depth() -> None:
    raw = (
        b"""Message-ID: <deep@example.com>
Date: Mon, 31 Aug 2026 15:00:00 +0200
From: Portal alerts <alerts@example.com>
Subject: Deep HTML
Content-Type: text/html; charset=utf-8

<html><body>"""
        + (b"<div>" * 101)
        + (b"</div>" * 101)
        + b"</body></html>"
    )

    with pytest.raises(AlertParseError, match="format-drift"):
        OtodomAlertParser().parse(raw)


def test_runtime_registers_olx_and_preserves_multi_value_source_policy() -> None:
    policy = SourcePolicy(
        key="olx",
        allowed_senders=frozenset(
            {"powiadomienia@marketing.olx.pl", "alerts@example.com"}
        ),
        allowed_hosts=frozenset({"www.olx.pl", "example.com"}),
        max_message_bytes=400_000,
    )

    parser = _portal_parser("olx", policy)

    assert isinstance(parser, OLXAlertParser)
    assert parser.allowed_senders == policy.allowed_senders
    assert parser.allowed_hosts == policy.allowed_hosts
    assert parser.max_message_bytes == 400_000
    assert "olx" in _gmail_pollers(Settings())


@pytest.mark.parametrize(
    ("parser_type", "tracking_host", "destination"),
    [
        (
            MorizonAlertParser,
            "link.morizon.pl",
            "https://www.morizon.pl/oferta/example-mzn2047000541?utm_source=email",
        ),
        (
            GratkaAlertParser,
            "link.gratka.pl",
            "https://gratka.pl/nieruchomosci/example/ob/48862197?utm_source=email",
        ),
    ],
)
def test_embedded_tracking_link_is_decoded_without_network(
    parser_type: type[OtodomAlertParser], tracking_host: str, destination: str
) -> None:
    encoded = _base64url(destination)
    tracking_url = f"https://{tracking_host}/click/example/{encoded}/signature"
    destination_host = destination.split("/", 3)[2]

    parsed = parser_type(allowed_hosts=frozenset({destination_host})).parse(
        _tracking_link_message(parser_type.source_key, tracking_url)
    )

    assert parsed.listing.canonical_url == destination.split("?", 1)[0]


@pytest.mark.parametrize(
    ("parser_type", "tracking_url", "destination"),
    [
        (
            OLXAlertParser,
            "https://clicks.marketing.olx.pl/f/a/safe-test-token",
            "https://www.olx.pl/d/oferta/example-CID3-ID1cbIrJ.html?utm_source=email",
        ),
        (
            OtodomAlertParser,
            "https://clicks.alerts.otodom.pl/f/a/safe-test-token",
            "https://www.otodom.pl/pl/oferta/example-ID4CVvA?lid=example",
        ),
    ],
)
def test_opaque_tracking_link_uses_one_hop_resolver(
    parser_type: type[OtodomAlertParser], tracking_url: str, destination: str
) -> None:
    calls: list[tuple[str, float]] = []

    def fetch_redirect(url: str, timeout_seconds: float) -> tuple[int, tuple[str, ...]]:
        calls.append((url, timeout_seconds))
        return 302, (destination,)

    parser = parser_type(
        allowed_hosts=frozenset({destination.split("/", 3)[2]}),
        redirect_fetcher=fetch_redirect,
        redirect_timeout_seconds=3.0,
    )
    raw = _tracking_link_message(parser_type.source_key, tracking_url)

    first = parser.parse(raw)
    second = parser.parse(raw)

    assert first.listing.canonical_url == destination.split("?", 1)[0]
    assert second.listing.canonical_url == first.listing.canonical_url
    assert calls == [(tracking_url, 3.0)]


@pytest.mark.parametrize(
    ("status", "locations"),
    [
        (200, ("https://www.olx.pl/d/oferta/example-CID3-ID1.html",)),
        (302, ()),
        (
            302,
            (
                "https://www.olx.pl/d/oferta/example-CID3-ID1.html",
                "https://www.olx.pl/d/oferta/other-CID3-ID2.html",
            ),
        ),
        (302, ("https://attacker.example/d/oferta/example-CID3-ID1.html",)),
        (302, ("https://www.olx.pl/nieruchomosci/mieszkania/",)),
    ],
)
def test_olx_redirect_rejects_unsafe_or_non_listing_destination(
    status: int, locations: tuple[str, ...]
) -> None:
    parser = OLXAlertParser(
        allowed_hosts=frozenset({"www.olx.pl"}),
        redirect_fetcher=lambda _url, _timeout: (status, locations),
    )

    with pytest.raises(AlertParseError, match="listing-url"):
        parser.parse(
            _tracking_link_message(
                "olx", "https://clicks.marketing.olx.pl/f/a/safe-test-token"
            )
        )


def test_embedded_tracking_link_rejects_invalid_base64_and_destination() -> None:
    parser = MorizonAlertParser(allowed_hosts=frozenset({"www.morizon.pl"}))

    for encoded in (
        "not!base64",
        _base64url("https://attacker.example/oferta/example"),
    ):
        with pytest.raises(AlertParseError, match="listing-url"):
            parser.parse(
                _tracking_link_message(
                    "morizon",
                    f"https://link.morizon.pl/click/example/{encoded}/signature",
                )
            )


def test_generic_parser_does_not_resolve_irrelevant_tracking_link() -> None:
    calls: list[str] = []

    def fetch_redirect(url: str, _timeout: float) -> tuple[int, tuple[str, ...]]:
        calls.append(url)
        return 302, ("https://www.olx.pl/d/oferta/unexpected-CID3-ID1.html",)

    raw = b"""Message-ID: <olx-irrelevant@example.com>
Date: Mon, 31 Aug 2026 15:00:00 +0200
From: Portal alerts <alerts@example.com>
Subject: Listing and preferences
Content-Type: text/html; charset=utf-8

<html><body>
  <p><a href="https://clicks.marketing.olx.pl/f/a/preferences">Preferences</a></p>
  <div class="listing-card">
    <h2><a href="https://www.olx.pl/d/oferta/example-CID3-ID1.html">Example</a></h2>
    <p class="location">Example District</p>
    <p>750000 PLN | 55 m2 | 3 pokoje</p>
  </div>
</body></html>"""
    parser = OLXAlertParser(
        allowed_hosts=frozenset({"www.olx.pl"}),
        redirect_fetcher=fetch_redirect,
    )

    parsed = parser.parse(raw)

    assert parsed.listing.canonical_url == (
        "https://www.olx.pl/d/oferta/example-CID3-ID1.html"
    )
    assert calls == []


@pytest.mark.parametrize(
    "tracking_url",
    [
        "http://clicks.marketing.olx.pl/f/a/test",
        "https://clicks.marketing.olx.pl:444/f/a/test",
        "https://clicks.marketing.olx.pl@127.0.0.1/f/a/test",
    ],
)
def test_redirect_resolver_rejects_non_exact_tracking_origin_without_request(
    tracking_url: str,
) -> None:
    calls: list[str] = []

    def fetch_redirect(url: str, _timeout: float) -> tuple[int, tuple[str, ...]]:
        calls.append(url)
        return 302, ("https://www.olx.pl/d/oferta/example-CID3-ID1.html",)

    parser = OLXAlertParser(
        allowed_hosts=frozenset({"www.olx.pl"}),
        redirect_fetcher=fetch_redirect,
    )

    with pytest.raises(AlertParseError, match="listing-url"):
        parser.parse(_tracking_link_message("olx", tracking_url))
    assert calls == []
