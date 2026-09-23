"""Portal-owned bounded JSON-LD baseline; no transport or shared extraction."""

import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any, cast

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
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self.current, self.current_kind = [], "jsonld"
        elif tag == "script" and attributes.get("id") == "__NUXT_DATA__":
            self.current, self.current_kind = [], "nuxt"

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.current is not None:
            target = self.blocks if self.current_kind == "jsonld" else self.nuxt_blocks
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


def _integer(value: object, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,9}", text):
        return None
    result = int(text)
    return result if 0 < result <= maximum else None


def _decimal_text(value: object, maximum: Decimal) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = str(value).strip().replace(",", ".")
    if not re.fullmatch(r"[0-9]{1,6}(\.[0-9]{1,4})?", text):
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return text if amount.is_finite() and 0 < amount <= maximum else None


def _label_money(value: object, maximum: int) -> int | None:
    if not isinstance(value, str):
        return None
    normalized = value.replace("\xa0", " ").replace(",", ".")
    match = re.search(r"(?<![0-9])([0-9][0-9 .]*[0-9]|[0-9])(?![0-9])", normalized)
    if match is None:
        return None
    return _money(match.group(1).replace(" ", ""), maximum)


def _hydrate_nuxt(value: object) -> object:
    if not isinstance(value, list) or not 1 <= len(value) <= 50_000:
        raise ValueError("invalid Nuxt payload")
    cache: dict[int, object] = {}
    resolving: set[int] = set()

    def reference(item: object, depth: int = 0) -> object:
        if depth > 32:
            raise ValueError("Nuxt payload is too deep")
        if isinstance(item, bool) or not isinstance(item, int):
            return literal(item, depth)
        if item < 0:
            return None
        if item >= len(value) or item in resolving:
            raise ValueError("invalid Nuxt reference")
        if item in cache:
            return cache[item]
        resolving.add(item)
        result = literal(value[item], depth + 1)
        cache[item] = result
        resolving.remove(item)
        return result

    def literal(item: object, depth: int) -> object:
        if isinstance(item, dict):
            result: dict[str, object] = {}
            for key, nested in item.items():
                if not isinstance(key, str) or len(key) > 200:
                    raise ValueError("invalid Nuxt key")
                result[key] = reference(nested, depth + 1)
            return result
        if isinstance(item, list):
            return [reference(nested, depth + 1) for nested in item]
        return item

    return reference(0)


def _nested_dicts(value: object) -> list[dict[str, object]]:
    result = []
    pending = [value]
    seen: set[int] = set()
    while pending and len(seen) <= 50_000:
        item = pending.pop()
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(item, dict):
            result.append(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return result


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
        matches: list[tuple[int, str, dict[str, Any]]] = []
        for index, block in enumerate(document.blocks):
            try:
                value = json.loads(block)
            except (ValueError, RecursionError):
                continue
            # Only explicit top-level or @graph residence nodes are candidates.
            # Recommendations and arbitrary nested Product nodes stay excluded.
            nodes: list[tuple[str, object]] = [("", value)]
            if isinstance(value, list):
                nodes = [(f"[{position}]", node) for position, node in enumerate(value)]
            elif isinstance(value, dict) and isinstance(value.get("@graph"), list):
                nodes = [
                    (f".@graph[{position}]", node)
                    for position, node in enumerate(value["@graph"])
                ]
            for path, node in nodes:
                if isinstance(node, dict) and node.get("@type") in (
                    "Apartment",
                    "House",
                ):
                    matches.append((index, path, node))
        nuxt_matches: list[tuple[int, dict[str, object]]] = []
        for index, block in enumerate(document.nuxt_blocks):
            try:
                hydrated = _hydrate_nuxt(json.loads(block))
            except (ValueError, RecursionError):
                continue
            property_data: dict[int, dict[str, object]] = {
                id(item["propertyData"]): cast(dict[str, object], item["propertyData"])
                for item in _nested_dicts(hydrated)
                if isinstance(item.get("propertyData"), dict)
            }
            if len(property_data) == 1:
                nuxt_matches.append((index, next(iter(property_data.values()))))
            elif property_data:
                return self._unknown(page)
        if len(matches) + len(nuxt_matches) != 1:
            return self._unknown(page)
        if nuxt_matches:
            return self._parse_nuxt(page, *nuxt_matches[0])
        index, node_path, listing = matches[0]
        offers_value = listing.get("offers")
        offers = (
            _mapping(offers_value[0])
            if isinstance(offers_value, list) and len(offers_value) == 1
            else _mapping(offers_value)
        )
        offer_path = "offers[0]" if isinstance(offers_value, list) else "offers"
        raw: dict[str, tuple[str, object, str]] = {
            "title": ("title", listing.get("name"), "name"),
            "price": (
                "price_minor",
                _money(offers.get("price"), 100_000_000_000_000),
                f"{offer_path}.price",
            ),
            "currency": (
                "currency",
                offers.get("priceCurrency"),
                f"{offer_path}.priceCurrency",
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
                f"{offer_path}.availability",
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
                    f"script[type=application/ld+json][{index}]{node_path}.{locator}",
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
            "jsonld-graph-residence-v2" if node_path else "jsonld-residence-v1",
            tuple(candidates),
            tuple(name for name in DECLARED_FIELDS[:11] if name not in present),
            PageFacts(**facts),
            fields,
        )

    def _parse_nuxt(
        self, page: PageInput, index: int, listing: dict[str, object]
    ) -> ParserResult:
        price = _mapping(listing.get("price"))
        location = _mapping(listing.get("location"))
        detail_rows: list[dict[str, object]] = []
        for key in (
            "detailedInformation",
            "offerDetailedInformation",
            "highlightedParameters",
            "buildingDetailedInformation",
        ):
            group = listing.get(key)
            if isinstance(group, list):
                detail_rows.extend(item for item in group if isinstance(item, dict))
        details = {
            str(item.get("label", "")).strip().casefold(): item.get("value")
            for item in detail_rows
            if isinstance(item.get("label"), str)
        }
        admin_fee = next(
            (
                _label_money(value, 10_000_000)
                for label, value in details.items()
                if label
                in {
                    "czynsz",
                    "opłata administracyjna",
                    "opłaty administracyjne",
                    "administration fee",
                }
            ),
            None,
        )
        heating_value = next(
            (
                value
                for label, value in details.items()
                if label in {"ogrzewanie", "heating"}
            ),
            None,
        )
        heating = self._heating(heating_value)
        area = _decimal_text(listing.get("area"), Decimal("1000000"))
        locality, locality_locator = self._locality(location)
        price_per_sqm = _mapping(listing.get("priceM2"))
        raw: dict[str, tuple[str, object, str]] = {
            "title": ("title", listing.get("title"), "propertyData.title"),
            "price": (
                "price_minor",
                _money(price.get("amount"), 100_000_000_000_000),
                "propertyData.price.amount",
            ),
            "currency": (
                "currency",
                price.get("currency"),
                "propertyData.price.currency",
            ),
            "locality": (
                "location",
                locality,
                f"propertyData.location.{locality_locator}",
            ),
            "area": ("area_sqm", area, "propertyData.area"),
            "rooms": (
                "rooms",
                _integer(listing.get("numberOfRoomsCount"), 1000)
                or _integer(listing.get("numberOfRooms"), 1000),
                "propertyData.numberOfRoomsCount",
            ),
            "description": (
                "description",
                listing.get("description"),
                "propertyData.description",
            ),
            "monthly_admin_fee": (
                "monthly_admin_fee_minor",
                admin_fee,
                "propertyData.detailedInformation[label=administration-fee]",
            ),
            "heating_type": (
                "heating_type",
                heating,
                "propertyData.detailedInformation[label=heating]",
            ),
        }
        facts: dict[str, Any] = {"availability": "unknown"}
        candidates = []
        for name, (fact_name, value, locator) in raw.items():
            if value is None or isinstance(value, (dict, list)):
                continue
            if not isinstance(value, (str, int, bool)):
                continue
            if name in ("title", "locality", "description") and (
                not isinstance(value, str) or not value.strip()
            ):
                continue
            validated_value: Any = value
            try:
                PageFacts(**{fact_name: validated_value})
            except ValueError:
                continue
            facts[fact_name] = value
            candidates.append(
                FieldCandidate(
                    name,
                    value,
                    "page",
                    f"script#__NUXT_DATA__[{index}].{locator}",
                    self.release_hash,
                )
            )
        explicit_per_sqm = _money(price_per_sqm.get("amount"), 10_000_000_000)
        if explicit_per_sqm is not None:
            facts["price_per_sqm_minor"] = explicit_per_sqm
            candidates.append(
                FieldCandidate(
                    "price_per_sqm_minor",
                    explicit_per_sqm,
                    "page",
                    f"script#__NUXT_DATA__[{index}].propertyData.priceM2.amount",
                    self.release_hash,
                )
            )
        elif "price_minor" in facts and "area_sqm" in facts:
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
            ResolvedField(
                name, FieldState.VALUE, present[name].value, present[name].origin
            )
            if name in present
            else ResolvedField(name, FieldState.UNKNOWN)
            for name in DECLARED_FIELDS
        )
        return ParserResult(
            page.capture_id,
            self.release_hash,
            "nuxt-property-v1",
            tuple(candidates),
            tuple(name for name in DECLARED_FIELDS[:11] if name not in present),
            PageFacts(**facts),
            fields,
        )

    @staticmethod
    def _locality(location: dict[str, Any]) -> tuple[str | None, str]:
        commune = location.get("commune")
        if isinstance(commune, str) and commune.strip():
            return commune, "commune"
        path = location.get("location")
        if isinstance(path, list):
            # Gratka paths put county-level localities after the county and direct
            # city localities immediately after the voivodeship. Deeper elements
            # are districts or neighbourhoods and must not replace the locality.
            index = 2 if isinstance(location.get("county"), str) else 1
            if (
                len(path) > index
                and isinstance(path[index], str)
                and path[index].strip()
            ):
                return path[index], f"location[{index}]"
        return None, "location"

    @staticmethod
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
