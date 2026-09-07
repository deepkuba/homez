import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from homefinder.scraper.app import create_scraper_app
from homefinder.sources.portal_pages import (
    PageScrapeError,
    PortalPageScraper,
    ScrapedListing,
)
from homefinder.sources.remote_scraper import RemotePortalScraper

PORTALS = (
    (
        "olx",
        "https://www.olx.pl/d/oferta/jasne-mieszkanie-IDABC123.html",
        "IDABC123",
    ),
    (
        "otodom",
        "https://www.otodom.pl/pl/oferta/jasne-mieszkanie-ID4xyz",
        "ID4xyz",
    ),
    (
        "morizon",
        "https://www.morizon.pl/oferta/sprzedaz-jasne-mieszkanie-mzn2047000541",
        "mzn2047000541",
    ),
    (
        "gratka",
        "https://gratka.pl/nieruchomosci/jasne-mieszkanie/ob/48862197",
        "48862197",
    ),
)


def _page(url: str) -> bytes:
    payload = {
        "@context": "https://schema.org",
        "@type": "Apartment",
        "url": url,
        "name": "Jasne mieszkanie z balkonem",
        "description": "Po remoncie, blisko tramwaju.",
        "floorSize": {"@type": "QuantitativeValue", "value": "74.5"},
        "numberOfRooms": 3,
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "Stelmachow 10",
            "addressLocality": "Krakow",
        },
        "offers": {
            "@type": "Offer",
            "price": "1234567.89",
            "priceCurrency": "PLN",
            "availability": "https://schema.org/InStock",
        },
    }
    return (
        "<html><head><script type='application/ld+json'>"
        + json.dumps(payload)
        + "</script></head></html>"
    ).encode()


@pytest.mark.parametrize(("source", "url", "listing_id"), PORTALS)
def test_each_portal_scraper_extracts_normalized_structured_listing(
    source: str, url: str, listing_id: str
) -> None:
    scraper = PortalPageScraper(
        source,
        fetcher=lambda requested, timeout, limit: _page(requested),
    )

    listing = scraper.scrape(url)

    assert listing.source_key == source
    assert listing.source_listing_id.casefold() == listing_id.casefold()
    assert listing.canonical_url == url
    assert listing.title == "Jasne mieszkanie z balkonem"
    assert listing.price_minor == 123_456_789
    assert listing.currency == "PLN"
    assert listing.area_sqm == Decimal("74.5")
    assert listing.rooms == 3
    assert listing.location == "Stelmachow 10, Krakow"
    assert listing.description == "Po remoncie, blisko tramwaju."
    assert listing.availability == "active"


def test_portal_scraper_rejects_cross_portal_and_credentialed_urls() -> None:
    scraper = PortalPageScraper("olx", fetcher=lambda url, timeout, limit: b"")

    with pytest.raises(PageScrapeError, match="allowlisted"):
        scraper.scrape("https://attacker.example/d/oferta/item-IDABC.html")
    with pytest.raises(PageScrapeError, match="allowlisted"):
        scraper.scrape("https://user:password@www.olx.pl/d/oferta/item-IDABC.html")


def test_portal_scraper_bounds_and_validates_page_content() -> None:
    url = PORTALS[0][1]
    oversized = PortalPageScraper(
        "olx", max_page_bytes=100, fetcher=lambda url, timeout, limit: b"x" * 101
    )
    malformed = PortalPageScraper(
        "olx",
        fetcher=lambda url, timeout, limit: (
            b"<script type='application/ld+json'>{not-json}</script>"
        ),
    )

    with pytest.raises(PageScrapeError, match="size"):
        oversized.scrape(url)
    with pytest.raises(PageScrapeError, match="structured listing data"):
        malformed.scrape(url)


def test_morizon_offer_json_ld_uses_email_values_for_missing_optional_facts() -> None:
    url = PORTALS[2][1]
    page = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "Offer",
            "name": "Oferta mieszkania",
            "description": "Pelny opis",
            "price": "700000",
            "priceCurrency": "PLN",
            "url": url,
        }
    )
    scraper = PortalPageScraper(
        "morizon",
        fetcher=lambda requested, timeout, limit: (
            f"<script type='application/ld+json'>{page}</script>".encode()
        ),
    )

    result = scraper.scrape(url)

    assert result.price_minor == 70_000_000
    assert result.area_sqm is None
    assert result.rooms is None
    assert result.location is None


def test_scraper_api_requires_token_and_is_pinned_to_one_source(tmp_path) -> None:
    token_file = tmp_path / "scraper-token"
    token_file.write_text("test-shared-secret", encoding="ascii")
    token_file.chmod(0o600)
    expected = ScrapedListing(
        source_key="olx",
        source_listing_id="IDABC123",
        canonical_url=PORTALS[0][1],
        title="Jasne mieszkanie",
        price_minor=900_000_00,
        currency="PLN",
        area_sqm=Decimal("50.5"),
        rooms=2,
        location="Krakow",
        description="Opis",
        availability="active",
    )
    calls: list[str] = []

    def scrape(url: str) -> ScrapedListing:
        calls.append(url)
        return expected

    client = TestClient(create_scraper_app("olx", token_file=token_file, scrape=scrape))

    assert client.get("/health").json() == {"status": "ok", "source": "olx"}
    assert client.post("/scrape", json={"url": PORTALS[0][1]}).status_code == 401
    assert (
        client.post(
            "/scrape",
            headers={"Authorization": "Bearer wrong"},
            json={"url": PORTALS[0][1]},
        ).status_code
        == 401
    )
    response = client.post(
        "/scrape",
        headers={"Authorization": "Bearer test-shared-secret"},
        json={"url": PORTALS[0][1]},
    )
    cross_source = client.post(
        "/scrape",
        headers={"Authorization": "Bearer test-shared-secret"},
        json={"url": PORTALS[1][1]},
    )
    oversized = client.post(
        "/scrape",
        headers={"Authorization": "Bearer test-shared-secret"},
        content=b"x" * 4_097,
    )

    assert response.status_code == 200
    assert cross_source.status_code == 422
    assert oversized.status_code == 413
    assert response.json()["source_key"] == "olx"
    assert response.json()["area_sqm"] == "50.5"
    assert calls == [PORTALS[0][1]]


def test_remote_scraper_calls_nas_over_tailnet_and_validates_result(tmp_path) -> None:
    token_file = tmp_path / "scraper-token"
    token_file.write_text("test-shared-secret", encoding="ascii")
    token_file.chmod(0o600)
    requested: list[tuple[str, str, str]] = []

    def request(
        endpoint: str, url: str, token: str, timeout: float, limit: int
    ) -> bytes:
        requested.append((endpoint, url, token))
        return json.dumps(
            {
                "source_key": "olx",
                "source_listing_id": "IDABC123",
                "canonical_url": PORTALS[0][1],
                "title": "Jasne mieszkanie",
                "price_minor": 900_000_00,
                "currency": "PLN",
                "area_sqm": "50.5",
                "rooms": 2,
                "location": "Krakow",
                "description": "Opis",
                "availability": "active",
            }
        ).encode()

    scraper = RemotePortalScraper(
        "olx",
        endpoint="http://100.100.20.30:18101",
        token_file=token_file,
        requester=request,
    )

    result = scraper.scrape(PORTALS[0][1])

    assert result.area_sqm == Decimal("50.5")
    assert requested == [
        (
            "http://100.100.20.30:18101/scrape",
            PORTALS[0][1],
            "test-shared-secret",
        )
    ]


def test_remote_scraper_rejects_public_cleartext_endpoint(tmp_path) -> None:
    token_file = tmp_path / "scraper-token"

    with pytest.raises(ValueError, match="Tailscale"):
        RemotePortalScraper(
            "olx",
            endpoint="http://nas.example.com:18101",
            token_file=token_file,
        )
    with pytest.raises(ValueError, match="Tailscale"):
        RemotePortalScraper(
            "olx",
            endpoint="https://nas.example.com:18101",
            token_file=token_file,
        )
