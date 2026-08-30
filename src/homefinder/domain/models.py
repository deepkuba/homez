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


@dataclass(frozen=True, slots=True)
class ParsedAlert:
    source: Source
    message: EmailMessage
    listing: Listing
    snapshot: ListingSnapshot
