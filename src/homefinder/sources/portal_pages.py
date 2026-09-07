"""Bounded, source-pinned extraction of listing-page structured data."""

from __future__ import annotations

import http.client
import json
import math
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit, urlunsplit

MAX_PAGE_BYTES = 2_000_000
MAX_JSON_LD_BLOCKS = 64
MAX_JSON_LD_CHARS = 1_000_000
MAX_TEXT_CHARS = 20_000
PageFetcher = Callable[[str, float, int], bytes]


class PageScrapeError(ValueError):
    """A listing page could not be fetched or did not satisfy its contract."""


class PageFetchError(PageScrapeError):
    """A portal rejected or temporarily could not serve a page request."""

    def __init__(
        self, *, status_code: int, retry_after_seconds: int | None = None
    ) -> None:
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__("listing page returned an unexpected status")


@dataclass(frozen=True, slots=True)
class ScrapedListing:
    source_key: str
    source_listing_id: str
    canonical_url: str
    title: str
    price_minor: int | None
    currency: str | None
    area_sqm: Decimal | None
    rooms: int | None
    location: str | None
    description: str
    availability: str

    def as_json(self) -> dict[str, object]:
        return {
            "source_key": self.source_key,
            "source_listing_id": self.source_listing_id,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "price_minor": self.price_minor,
            "currency": self.currency,
            "area_sqm": str(self.area_sqm) if self.area_sqm is not None else None,
            "rooms": self.rooms,
            "location": self.location,
            "description": self.description,
            "availability": self.availability,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> ScrapedListing:
        try:
            area_value = payload.get("area_sqm")
            area = Decimal(str(area_value)) if area_value is not None else None
            price_value = payload.get("price_minor")
            rooms_value = payload.get("rooms")
            result = cls(
                source_key=_bounded_text(payload["source_key"], 100),
                source_listing_id=_bounded_text(payload["source_listing_id"], 255),
                canonical_url=_bounded_text(payload["canonical_url"], 2048),
                title=_bounded_text(payload["title"], 500),
                price_minor=(
                    _json_int(price_value) if price_value is not None else None
                ),
                currency=(
                    _currency(payload["currency"])
                    if payload.get("currency") is not None
                    else None
                ),
                area_sqm=area,
                rooms=_json_int(rooms_value) if rooms_value is not None else None,
                location=(
                    _bounded_text(payload["location"], 500)
                    if payload.get("location") is not None
                    else None
                ),
                description=_optional_text(payload.get("description"), 20_000) or "",
                availability=_bounded_text(payload.get("availability", "unknown"), 20),
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise PageScrapeError("scraper response is invalid") from error
        if (
            result.price_minor is not None
            and result.price_minor <= 0
            or result.price_minor is not None
            and result.price_minor > 100_000_000_000_000
            or result.area_sqm is not None
            and result.area_sqm <= 0
            or result.area_sqm is not None
            and result.area_sqm > 100_000
            or result.rooms is not None
            and result.rooms <= 0
            or result.rooms is not None
            and result.rooms > 1_000
        ):
            raise PageScrapeError("scraper response contains invalid numeric values")
        if result.availability not in {"active", "unavailable", "unknown"}:
            raise PageScrapeError("scraper response availability is invalid")
        return result


@dataclass(frozen=True, slots=True)
class _PortalContract:
    hosts: frozenset[str]
    path: re.Pattern[str]


_PORTALS = {
    "olx": _PortalContract(
        frozenset({"olx.pl", "www.olx.pl"}),
        re.compile(r"/d/oferta/[^/]+-(ID[^/.]+)\.html/?", re.IGNORECASE),
    ),
    "otodom": _PortalContract(
        frozenset({"otodom.pl", "www.otodom.pl"}),
        re.compile(r"/pl/oferta/[^/]*-(ID[0-9A-Za-z]+)(?:\.html)?/?", re.IGNORECASE),
    ),
    "morizon": _PortalContract(
        frozenset({"morizon.pl", "www.morizon.pl"}),
        re.compile(r"/oferta/[^/]*(mzn[0-9]+)/?", re.IGNORECASE),
    ),
    "gratka": _PortalContract(
        frozenset({"gratka.pl", "www.gratka.pl"}),
        re.compile(r"/nieruchomosci/.+/o[bi]/([0-9]+)/?", re.IGNORECASE),
    ),
}


def supported_portals() -> tuple[str, ...]:
    return tuple(_PORTALS)


class PortalPageScraper:
    """Fetch and parse one portal without accepting caller-selected hosts."""

    def __init__(
        self,
        source_key: str,
        *,
        timeout_seconds: float = 10.0,
        max_page_bytes: int = MAX_PAGE_BYTES,
        fetcher: PageFetcher | None = None,
    ) -> None:
        if source_key not in _PORTALS:
            raise ValueError("unsupported portal")
        if timeout_seconds <= 0 or max_page_bytes <= 0:
            raise ValueError("scraper limits must be positive")
        self.source_key = source_key
        self.timeout_seconds = timeout_seconds
        self.max_page_bytes = max_page_bytes
        self._fetcher = fetcher or _fetch_html

    def scrape(self, url: str) -> ScrapedListing:
        canonical_url, listing_id = validate_listing_url(self.source_key, url)
        try:
            body = self._fetcher(
                canonical_url, self.timeout_seconds, self.max_page_bytes
            )
        except PageScrapeError:
            raise
        except Exception as error:
            raise PageScrapeError("listing page could not be fetched") from error
        if not body or len(body) > self.max_page_bytes:
            raise PageScrapeError("listing page exceeds the size contract")
        try:
            html = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PageScrapeError("listing page is not valid UTF-8") from error
        data = _extract_structured_listing(html)
        return ScrapedListing(
            source_key=self.source_key,
            source_listing_id=listing_id,
            canonical_url=canonical_url,
            title=_bounded_text(data["title"], 500),
            price_minor=_price_minor(data.get("price")),
            currency=_currency(data.get("currency")),
            area_sqm=_area_sqm(data.get("area")),
            rooms=_positive_int(data.get("rooms")),
            location=_optional_text(data.get("location"), 500),
            description=_optional_text(data.get("description"), MAX_TEXT_CHARS) or "",
            availability=_availability(data.get("availability")),
        )


def validate_listing_url(source_key: str, url: str) -> tuple[str, str]:
    contract = _PORTALS.get(source_key)
    if contract is None:
        raise PageScrapeError("listing source is not supported")
    if not isinstance(url, str) or len(url) > 2048:
        raise PageScrapeError("listing URL is not allowlisted")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise PageScrapeError("listing URL is not allowlisted") from error
    host = parsed.hostname.casefold() if parsed.hostname else None
    if (
        parsed.scheme != "https"
        or host not in contract.hosts
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise PageScrapeError("listing URL is not allowlisted")
    match = contract.path.fullmatch(parsed.path)
    if match is None:
        raise PageScrapeError("listing URL is not allowlisted")
    canonical = urlunsplit(("https", host, parsed.path, "", ""))
    return canonical, match.group(1)


class _StructuredHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.json_blocks: list[str] = []
        self.meta: dict[str, str] = {}
        self._json_depth = 0
        self._json_chunks: list[str] = []
        self._json_chars = 0

    def handle_starttag(
        self, tag: str, attrs_list: list[tuple[str, str | None]]
    ) -> None:
        attrs = {key.casefold(): value or "" for key, value in attrs_list}
        if (
            tag == "script"
            and attrs.get("type", "").casefold() == "application/ld+json"
        ):
            if len(self.json_blocks) >= MAX_JSON_LD_BLOCKS:
                raise PageScrapeError("listing page has excessive structured data")
            self._json_depth = 1
            self._json_chunks = []
            self._json_chars = 0
        elif tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").casefold()
            content = attrs.get("content", "")
            if key and content and len(content) <= MAX_TEXT_CHARS:
                self.meta[key] = content

    def handle_data(self, data: str) -> None:
        if self._json_depth:
            self._json_chars += len(data)
            if self._json_chars > MAX_JSON_LD_CHARS:
                raise PageScrapeError("listing page has excessive structured data")
            self._json_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json_depth:
            self.json_blocks.append("".join(self._json_chunks))
            self._json_depth = 0
            self._json_chunks = []
            self._json_chars = 0


def _extract_structured_listing(html: str) -> dict[str, object]:
    parser = _StructuredHTMLParser()
    try:
        parser.feed(html)
    except (RecursionError, ValueError) as error:
        raise PageScrapeError("listing page HTML is invalid") from error
    candidates: list[dict[str, Any]] = []
    for block in parser.json_blocks:
        try:
            payload = json.loads(block)
        except (json.JSONDecodeError, RecursionError):
            continue
        try:
            candidates.extend(_listing_candidates(payload))
        except RecursionError as error:
            raise PageScrapeError(
                "listing structured data is too deeply nested"
            ) from error
    if not candidates:
        raise PageScrapeError("listing page has no structured listing data")
    candidate = max(candidates, key=_candidate_score)
    offers = _mapping_or_first(candidate.get("offers"))
    price_spec = _mapping_or_first(offers.get("priceSpecification"))
    address = _mapping_or_first(candidate.get("address"))
    area = _mapping_or_first(candidate.get("floorSize"))
    title = _first(candidate, "name", "headline") or parser.meta.get("og:title")
    if title is None:
        raise PageScrapeError("structured listing data is missing a title")
    return {
        "title": title,
        "description": candidate.get("description")
        or parser.meta.get("og:description"),
        "price": offers.get("price")
        or price_spec.get("price")
        or candidate.get("price"),
        "currency": offers.get("priceCurrency")
        or price_spec.get("priceCurrency")
        or candidate.get("priceCurrency"),
        "area": area.get("value")
        or candidate.get("area")
        or candidate.get("usableArea"),
        "rooms": candidate.get("numberOfRooms")
        or candidate.get("numberOfRoomsTotal")
        or candidate.get("rooms"),
        "location": _format_address(address) or candidate.get("location"),
        "availability": offers.get("availability") or candidate.get("availability"),
    }


def _listing_candidates(value: object) -> list[dict[str, Any]]:
    candidates = []
    for item in _walk(value):
        raw_type = item.get("@type", "")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        normalized = {str(value).casefold() for value in types}
        if normalized & {
            "apartment",
            "house",
            "offer",
            "product",
            "realestatelisting",
            "residence",
            "singlefamilyresidence",
        }:
            candidates.append(item)
    return candidates


def _walk(value: object) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _candidate_score(candidate: Mapping[str, object]) -> int:
    return sum(
        key in candidate
        for key in (
            "name",
            "headline",
            "offers",
            "floorSize",
            "numberOfRooms",
            "address",
        )
    )


def _mapping_or_first(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _first(value: Mapping[str, object], *keys: str) -> object | None:
    for key in keys:
        if value.get(key) is not None:
            return value[key]
    return None


def _format_address(address: Mapping[str, object]) -> str | None:
    parts = []
    for key in ("streetAddress", "addressLocality", "addressRegion"):
        value = _optional_text(address.get(key), 500)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def _price_minor(value: object) -> int | None:
    parsed = _positive_decimal(value)
    if parsed is not None and parsed > Decimal("1000000000000"):
        raise PageScrapeError("structured listing price is out of range")
    return int(parsed * 100) if parsed is not None else None


def _area_sqm(value: object) -> Decimal | None:
    parsed = _positive_decimal(value)
    if parsed is not None and parsed > Decimal("100000"):
        raise PageScrapeError("structured listing area is out of range")
    return parsed


def _json_int(value: object) -> int:
    if isinstance(value, bool):
        raise PageScrapeError("scraper response integer is invalid")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise PageScrapeError("scraper response integer is invalid")


def _positive_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    normalized = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if normalized.count(",") == 1 and "." not in normalized:
        normalized = normalized.replace(",", ".")
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as error:
        raise PageScrapeError("structured listing numeric value is invalid") from error
    if not parsed.is_finite() or parsed <= 0:
        raise PageScrapeError("structured listing numeric value is invalid")
    return parsed


def _positive_int(value: object) -> int | None:
    parsed = _positive_decimal(value)
    if parsed is None:
        return None
    integer = int(parsed)
    if parsed != integer or integer > 1_000:
        raise PageScrapeError("structured listing room count is invalid")
    return integer


def _currency(value: object) -> str | None:
    if value is None:
        return None
    currency = _bounded_text(value, 3).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise PageScrapeError("structured listing currency is invalid")
    return currency


def _availability(value: object) -> str:
    normalized = str(value or "").casefold()
    if normalized.endswith(("instock", "onlineonly", "limitedavailability")):
        return "active"
    if normalized.endswith(("soldout", "discontinued", "outofstock")):
        return "unavailable"
    return "unknown"


def _optional_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return _bounded_text(normalized, limit) if normalized else None


def _bounded_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        raise PageScrapeError("structured listing text value is invalid")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > limit:
        raise PageScrapeError("structured listing text value is invalid")
    return normalized


def _fetch_html(url: str, timeout_seconds: float, max_bytes: int) -> bytes:
    parsed = urlsplit(url)
    if parsed.hostname is None:
        raise PageScrapeError("listing URL is not allowlisted")
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=timeout_seconds)
    target = urlunsplit(("", "", parsed.path, parsed.query, ""))
    try:
        connection.request(
            "GET",
            target,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "HomezPrivateBuyer/1.0",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise PageFetchError(
                status_code=response.status,
                retry_after_seconds=_retry_after_seconds(
                    response.getheader("Retry-After"), datetime.now(timezone.utc)
                ),
            )
        content_type = response.getheader("Content-Type", "").casefold()
        if not content_type.startswith(("text/html", "application/xhtml+xml")):
            raise PageScrapeError("listing page returned an unexpected content type")
        body = response.read(max_bytes + 1)
    except (OSError, http.client.HTTPException) as error:
        raise PageScrapeError("listing page could not be fetched") from error
    finally:
        connection.close()
    if len(body) > max_bytes:
        raise PageScrapeError("listing page exceeds the size contract")
    return body


def _retry_after_seconds(value: str | None, now: datetime) -> int | None:
    if value is None:
        return None
    try:
        seconds = int(value)
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            seconds = math.ceil((target - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return max(1, min(seconds, 86_400))
