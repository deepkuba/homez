"""Otodom-only parser; extraction logic is intentionally local to this package."""

import html
import json
import re
from decimal import ROUND_HALF_UP, Decimal

from homefinder.parsers.contracts import (
    FieldCandidate,
    PageFacts,
    PageInput,
    ParserResult,
    resolve_fields,
)

CORE_FIELDS = (
    "title",
    "price",
    "currency",
    "locality",
    "area",
    "rooms",
    "description",
    "availability",
    "monthly_admin_fee",
    "heating_type",
    "admin_fee_includes_heating",
)


class OtodomPageParser:
    def __init__(self, release_hash: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", release_hash) is None:
            raise ValueError("invalid parser release")
        self.release_hash = release_hash

    def parse(self, page: PageInput) -> ParserResult:
        try:
            text = page.body.decode("utf-8")
        except UnicodeDecodeError:
            return self._unknown(page)
        markers = re.findall(
            r'<meta\s+name="otodom:variant"\s+content="listing-v1"\s*/?>', text
        )
        if len(markers) != 1:
            return self._unknown(page)
        scripts = re.findall(
            r'<script\s+type="application/ld\+json">(.*?)</script>', text, re.DOTALL
        )
        if len(scripts) != 1:
            return self._unknown(page)
        try:
            data = json.loads(html.unescape(scripts[0]))
            summary = dict(re.findall(r"<dt>([^<]+)</dt><dd>([^<]*)</dd>", text))
            price = int(Decimal(str(data["offers"]["price"])) * 100)
            area = str(data["floorSize"]["value"])
            values = {
                "title": str(data["name"]),
                "price": price,
                "currency": str(data["offers"]["priceCurrency"]),
                "locality": str(data["address"]["addressLocality"]),
                "area": area,
                "rooms": int(data["numberOfRooms"]),
                "description": str(data["description"]),
                "availability": summary["availability"],
                "monthly_admin_fee": int(summary["monthly_admin_fee"]),
                "heating_type": summary["heating_type"],
                "admin_fee_includes_heating": summary["admin_fee_includes_heating"]
                == "true",
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._unknown(page)
        candidates = tuple(
            FieldCandidate(
                name,
                value,
                "summary" if name in summary else "attributes",
                name,
                self.release_hash,
            )
            for name, value in values.items()
        )
        per_sqm = int(
            (Decimal(price) / Decimal(area)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        candidates += (
            FieldCandidate(
                "price_per_sqm_minor",
                per_sqm,
                "derived",
                "price/area",
                self.release_hash,
            ),
        )
        fields = resolve_fields(candidates)
        return ParserResult(
            page.capture_id,
            self.release_hash,
            "listing-v1",
            candidates,
            (),
            facts=PageFacts(
                title=values["title"],
                price_minor=price,
                currency=values["currency"],
                area_sqm=area,
                rooms=values["rooms"],
                location=values["locality"],
                description=values["description"],
                availability=values["availability"],
                monthly_admin_fee_minor=values["monthly_admin_fee"],
                heating_type=values["heating_type"],
                admin_fee_includes_heating=values["admin_fee_includes_heating"],
                price_per_sqm_minor=per_sqm,
            ),
            fields=fields,
        )

    def _unknown(self, page: PageInput) -> ParserResult:
        return ParserResult(
            page.capture_id, self.release_hash, "unknown-variant", (), CORE_FIELDS
        )
