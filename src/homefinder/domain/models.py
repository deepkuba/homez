from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Source:
    id: UUID
    key: str
    display_name: str


@dataclass(frozen=True, slots=True)
class EmailMessage:
    id: UUID
    source_id: UUID
    provider_message_id: str
    received_at: datetime
    sender: str
    subject: str
    raw_sha256: str
    parser_version: str = "legacy-v1"


@dataclass(frozen=True, slots=True)
class Listing:
    id: UUID
    source_id: UUID
    source_listing_id: str
    canonical_url: str
    title: str


@dataclass(frozen=True, slots=True)
class ListingSnapshot:
    id: UUID
    listing_id: UUID
    observed_at: datetime
    price_minor: int
    currency: str
    area_sqm: Decimal
    rooms: int
    availability: str
    location: str
    description: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class PropertyCandidate:
    id: UUID
    listing_ids: tuple[UUID, ...]
    deterministic_key: str


@dataclass(frozen=True, slots=True)
class DuplicateEvidence:
    listing_id: UUID
    possible_listing_id: UUID
    confidence: Decimal
    reasons: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class ResurfaceDecision:
    resurface: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ParsedListing:
    listing: Listing
    snapshot: ListingSnapshot


@dataclass(frozen=True, slots=True)
class ParsedAlert:
    source: Source
    message: EmailMessage
    items: tuple[ParsedListing, ...]

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("an alert must contain at least one listing")

    @property
    def listing(self) -> Listing:
        """Compatibility accessor for callers rendering the first listing."""
        return self.items[0].listing

    @property
    def snapshot(self) -> ListingSnapshot:
        """Compatibility accessor for callers rendering the first snapshot."""
        return self.items[0].snapshot
