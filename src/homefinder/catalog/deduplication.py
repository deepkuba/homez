"""Deterministic identity and conservative duplicate-evidence rules."""

import re
import unicodedata
from decimal import Decimal

from homefinder.domain.models import Listing, ListingSnapshot


def deterministic_duplicate_key(listing: Listing, snapshot: ListingSnapshot) -> str:
    """Return a stable key from facts available in alert emails.

    The title is included deliberately: a district, room count, and area alone
    are too coarse and would merge unrelated homes in the same neighborhood.
    """
    return "|".join(
        (
            normalize_text(snapshot.location),
            normalize_text(listing.title),
            str(snapshot.rooms),
            str(snapshot.area_sqm.quantize(Decimal("0.1"))),
        )
    )


def duplicate_evidence(
    listing: Listing,
    snapshot: ListingSnapshot,
    other_listing: Listing,
    other_snapshot: ListingSnapshot,
) -> tuple[Decimal, tuple[str, ...]]:
    """Score a pair without merging it; human review decides fuzzy matches."""
    reasons: list[str] = []
    location = normalize_text(snapshot.location)
    other_location = normalize_text(other_snapshot.location)
    title = normalize_text(listing.title)
    other_title = normalize_text(other_listing.title)
    if location == other_location:
        reasons.append("same normalized location")
    if title == other_title:
        reasons.append("same normalized title")
    if snapshot.rooms == other_snapshot.rooms:
        reasons.append("same room count")
    area_delta = abs(snapshot.area_sqm - other_snapshot.area_sqm)
    if area_delta <= Decimal("1.0"):
        reasons.append("area differs by at most 1 m²")
    if snapshot.price_minor and other_snapshot.price_minor:
        price_delta = abs(snapshot.price_minor - other_snapshot.price_minor)
        if Decimal(price_delta) / max(
            snapshot.price_minor, other_snapshot.price_minor
        ) <= Decimal("0.05"):
            reasons.append("price differs by at most 5%")
    confidence = Decimal("0")
    confidence += Decimal("0.35") if location == other_location else Decimal("0")
    confidence += Decimal("0.25") if title == other_title else Decimal("0")
    confidence += (
        Decimal("0.15") if snapshot.rooms == other_snapshot.rooms else Decimal("0")
    )
    confidence += Decimal("0.15") if area_delta <= Decimal("1.0") else Decimal("0")
    confidence += (
        Decimal("0.10") if "price differs by at most 5%" in reasons else Decimal("0")
    )
    return confidence, tuple(reasons)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
