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
        self.nuxt_blocks: list[str] = []
        self.current: list[str] | None = None
        self.current_kind: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attributes = dict(attrs)
        if attributes.get("type") == "application/ld+json":
            self.current, self.current_kind = [], "jsonld"
        elif (
            attributes.get("type") == "application/json"
            and attributes.get("id") == "__NUXT_DATA__"
        ):
            self.current, self.current_kind = [], "nuxt"

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.current is not None:
            target = self.nuxt_blocks if self.current_kind == "nuxt" else self.blocks
            target.append("".join(self.current))
            self.current = None
            self.current_kind = None


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


def _reference(values: list[Any], reference: object) -> object:
    if (
        isinstance(reference, bool)
        or not isinstance(reference, int)
        or not 0 <= reference < len(values)
    ):
        return None
    return values[reference]


def _nuxt_property(blocks: list[str]) -> tuple[dict[str, Any], list[Any]] | None:
    if len(blocks) != 1:
        return None
    try:
        values = json.loads(blocks[0])
        if not isinstance(values, list) or not 1 <= len(values) <= 100_000:
            return None
        references = {
            value["propertyData"]
            for value in values
            if isinstance(value, dict) and "propertyData" in value
        }
        if len(references) != 1:
            return None
        selected = _reference(values, references.pop())
        if not isinstance(selected, dict):
            return None
        return selected, values
    except (KeyError, TypeError, ValueError, RecursionError):
        return None


def _detail_value(
    property_data: dict[str, Any], values: list[Any], section: str, label: str
) -> object:
    records = _reference(values, property_data.get(section))
    if not isinstance(records, list):
        return None
    matches = []
    for record_reference in records:
        record = _reference(values, record_reference)
        if not isinstance(record, dict):
            continue
        if _reference(values, record.get("label")) == label:
            matches.append(_reference(values, record.get("value")))
    return matches[0] if len(matches) == 1 else None


def _heating(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.casefold()
    for needles, result in (
        (("miejsk", "sieciow", "district"), "district"),
        (("gaz", "gas"), "gas"),
        (("elektr", "electric"), "electric"),
        (("pompa ciep", "heat pump"), "heat_pump"),
        (("węgl", "wegiel", "coal", "solid"), "solid_fuel"),
    ):
        if any(needle in normalized for needle in needles):
            return result
    return "other" if normalized.strip() else None


class MorizonPageParser:
    """One explicit residence representation, independently versioned by release."""

    def __init__(self, release_hash: str) -> None:
        self.release_hash = release_hash

    def parse(self, page: PageInput) -> ParserResult:
        document = _Document()
        try:
            document.feed(page.body.decode("utf-8", errors="strict"))
        except (UnicodeError, ValueError, RecursionError):
            return self._unknown(page)
        matches: list[tuple[int, str, dict[str, Any]]] = []
        for index, block in enumerate(document.blocks):
            try:
                value = json.loads(block)
            except (ValueError, RecursionError):
                continue
            # Morizon owns an explicit WebPage.mainEntity representation. Other
            # nested objects and recommendation cards are never candidates.
            nodes: list[tuple[str, object]] = [("", value)]
            if isinstance(value, dict) and "mainEntity" in value:
                nodes.append((".mainEntity", value.get("mainEntity")))
            for path, node in nodes:
                if isinstance(node, dict) and node.get("@type") in (
                    "Apartment",
                    "House",
                    "Offer",
                ):
                    matches.append((index, path, node))
        if len(matches) != 1:
            return self._unknown(page)
        index, node_path, listing = matches[0]
        is_offer = listing.get("@type") == "Offer"
        nuxt = _nuxt_property(document.nuxt_blocks) if is_offer else None
        if is_offer and nuxt is None:
            return self._unknown(page)
        property_data, nuxt_values = nuxt if nuxt is not None else ({}, [])
        offers = listing if is_offer else _mapping(listing.get("offers"))
        location = None
        if is_offer:
            location_record = _reference(nuxt_values, property_data.get("location"))
            if isinstance(location_record, dict):
                hierarchy = _reference(nuxt_values, location_record.get("location"))
                if isinstance(hierarchy, list):
                    resolved = [_reference(nuxt_values, item) for item in hierarchy]
                    if resolved and all(
                        isinstance(item, str) and item.strip() for item in resolved
                    ):
                        location = resolved[2] if len(resolved) >= 3 else resolved[-1]
        heating = (
            _heating(
                _detail_value(
                    property_data,
                    nuxt_values,
                    "buildingDetailedInformation",
                    "Ogrzewanie",
                )
            )
            if is_offer
            else listing.get("heatingType")
        )
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
                location
                if is_offer
                else _mapping(listing.get("address")).get("addressLocality"),
                "propertyData.location.location[city]"
                if is_offer
                else "address.addressLocality",
            ),
            "area": (
                "area_sqm",
                _reference(nuxt_values, property_data.get("area"))
                if is_offer
                else _mapping(listing.get("floorSize")).get("value"),
                "propertyData.area" if is_offer else "floorSize.value",
            ),
            "rooms": (
                "rooms",
                _reference(nuxt_values, property_data.get("numberOfRooms"))
                if is_offer
                else listing.get("numberOfRooms"),
                "propertyData.numberOfRooms" if is_offer else "numberOfRooms",
            ),
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
            "heating_type": (
                "heating_type",
                heating,
                "propertyData.buildingDetailedInformation[Ogrzewanie]"
                if is_offer
                else "heatingType",
            ),
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
                    (
                        f"script[type=application/json]#__NUXT_DATA__.{locator}"
                        if locator.startswith("propertyData.")
                        else f"script[type=application/ld+json][{index}]"
                        f"{node_path}.{locator}"
                    ),
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
            (
                "jsonld-offer-nuxt-property-v3"
                if is_offer
                else "jsonld-main-entity-residence-v2"
                if node_path
                else "jsonld-residence-v1"
            ),
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
