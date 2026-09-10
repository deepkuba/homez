"""Portal-owned bounded JSON-LD baseline; no transport or shared extraction."""

import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

from homefinder.parsers.contracts import (
    DECLARED_FIELDS,
    FieldCandidate,
    FieldState,
    PageFacts,
    PageInput,
    ParserResult,
    ResolvedField,
)


class _Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self.current = []

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.current is not None:
            self.blocks.append("".join(self.current))
            self.current = None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _money(value: object, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        amount = Decimal(str(value)) * 100
        if (
            amount.is_finite()
            and 0 < amount <= maximum
            and amount == amount.to_integral()
        ):
            return int(amount)
    except InvalidOperation:
        pass
    return None


class GratkaPageParser:
    """One explicit residence representation, independently versioned by release."""

    def __init__(self, release_hash: str) -> None:
        self.release_hash = release_hash

    def parse(self, page: PageInput) -> ParserResult:
        document = _Document()
        try:
            document.feed(page.body.decode("utf-8", errors="strict"))
        except (UnicodeError, ValueError, RecursionError):
            return self._unknown(page)
        matches: list[tuple[int, dict[str, Any]]] = []
        for index, block in enumerate(document.blocks):
            try:
                value = json.loads(block)
            except (ValueError, RecursionError):
                continue
            # A positive typed top-level residence is the baseline marker. Nested
            # recommendations and unrelated Product nodes are never candidates.
            if isinstance(value, dict) and value.get("@type") in ("Apartment", "House"):
                matches.append((index, value))
        if len(matches) != 1:
            return self._unknown(page)
        index, listing = matches[0]
        offers = _mapping(listing.get("offers"))
        raw: dict[str, tuple[str, object, str]] = {
            "title": ("title", listing.get("name"), "name"),
            "price": (
                "price_minor",
                _money(offers.get("price"), 100_000_000_000_000),
                "offers.price",
            ),
            "currency": (
                "currency",
                offers.get("priceCurrency"),
                "offers.priceCurrency",
            ),
            "locality": (
                "location",
                _mapping(listing.get("address")).get("addressLocality"),
                "address.addressLocality",
            ),
            "area": (
                "area_sqm",
                _mapping(listing.get("floorSize")).get("value"),
                "floorSize.value",
            ),
            "rooms": ("rooms", listing.get("numberOfRooms"), "numberOfRooms"),
            "description": ("description", listing.get("description"), "description"),
            "availability": (
                "availability",
                {
                    "https://schema.org/InStock": "active",
                    "InStock": "active",
                    "https://schema.org/OutOfStock": "unavailable",
                    "https://schema.org/SoldOut": "unavailable",
                }.get(str(offers.get("availability"))),
                "offers.availability",
            ),
            "monthly_admin_fee": (
                "monthly_admin_fee_minor",
                _money(listing.get("monthlyAdminFee"), 10_000_000),
                "monthlyAdminFee",
            ),
            "heating_type": ("heating_type", listing.get("heatingType"), "heatingType"),
            "admin_fee_includes_heating": (
                "admin_fee_includes_heating",
                listing.get("adminFeeIncludesHeating"),
                "adminFeeIncludesHeating",
            ),
        }
        facts: dict[str, Any] = {}
        candidates = []
        for name, (fact_name, value, locator) in raw.items():
            if value is None or isinstance(value, (dict, list)):
                continue
            if name in ("title", "locality", "description") and (
                not isinstance(value, str) or not value.strip()
            ):
                continue
            if name == "rooms" and type(value) is not int:
                continue
            if name == "admin_fee_includes_heating" and type(value) is not bool:
                continue
            if name == "area":
                value = str(value)
                if (
                    not re.fullmatch(r"[0-9]{1,6}(\.[0-9]{1,4})?", value)
                    or Decimal(value) <= 0
                ):
                    continue
            try:
                PageFacts(**{fact_name: value})
            except ValueError:
                continue
            facts[fact_name] = value
            candidates.append(
                FieldCandidate(
                    name,
                    value,
                    "page",
                    f"script[type=application/ld+json][{index}].{locator}",
                    self.release_hash,
                )
            )
        if "price_minor" in facts and "area_sqm" in facts:
            per_sqm = int(
                (Decimal(facts["price_minor"]) / Decimal(facts["area_sqm"])).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            facts["price_per_sqm_minor"] = per_sqm
            candidates.append(
                FieldCandidate(
                    "price_per_sqm_minor",
                    per_sqm,
                    "derived",
                    "price/area",
                    self.release_hash,
                )
            )
        present = {candidate.name: candidate for candidate in candidates}
        fields = tuple(
            ResolvedField(name, FieldState.VALUE, present[name].value, "page")
            if name in present
            else ResolvedField(name, FieldState.UNKNOWN)
            for name in DECLARED_FIELDS
        )
        return ParserResult(
            page.capture_id,
            self.release_hash,
            "jsonld-residence-v1",
            tuple(candidates),
            tuple(name for name in DECLARED_FIELDS[:11] if name not in present),
            PageFacts(**facts),
            fields,
        )

    def _unknown(self, page: PageInput) -> ParserResult:
        return ParserResult(
            page.capture_id,
            self.release_hash,
            "unknown-variant",
            (),
            DECLARED_FIELDS[:11],
            fields=tuple(
                ResolvedField(name, FieldState.UNKNOWN) for name in DECLARED_FIELDS
            ),
        )
