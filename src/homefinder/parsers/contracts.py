"""Inert typed parser boundary; no transport or persistence dependencies."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Portal = Literal["gratka", "morizon", "otodom", "olx"]
MAX_PAGE_BYTES = 2_000_000
DECLARED_FIELDS = (
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
    "price_per_sqm_minor",
)


@dataclass(frozen=True)
class PageInput:
    capture_id: UUID
    fetched_at: datetime
    body: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.body) > MAX_PAGE_BYTES:
            raise ValueError("parser input exceeds 2 MB")
        if self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")


@dataclass(frozen=True)
class FieldCandidate:
    name: str
    value: str | int | bool | None = field(repr=False)
    origin: str
    locator: str
    release_hash: str


class FieldState(str, Enum):
    VALUE = "value"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ResolvedField:
    name: str
    state: FieldState
    value: str | int | bool | None = field(default=None, repr=False)
    selected_origin: str | None = None


class PageFacts(BaseModel):
    """Bounded normalized values; no identity, raw document, or transport state."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    title: str = Field(default="", max_length=500, repr=False)
    price_minor: int | None = Field(default=None, gt=0, le=100_000_000_000_000)
    currency: Literal["PLN", "EUR", "USD"] | None = None
    area_sqm: str | None = Field(default=None, pattern=r"^[0-9]{1,6}(\.[0-9]{1,4})?$")
    rooms: int | None = Field(default=None, gt=0, le=1000)
    location: str | None = Field(default=None, max_length=500, repr=False)
    description: str = Field(default="", max_length=20_000, repr=False)
    availability: Literal["active", "unavailable", "unknown"] = "unknown"
    monthly_admin_fee_minor: int | None = Field(default=None, gt=0, le=10_000_000)
    heating_type: (
        Literal["district", "gas", "electric", "heat_pump", "solid_fuel", "other"]
        | None
    ) = None
    admin_fee_includes_heating: bool | None = None
    price_per_sqm_minor: int | None = Field(default=None, gt=0, le=10_000_000_000)


@dataclass(frozen=True)
class ParserResult:
    capture_id: UUID
    release_hash: str
    variant: str
    candidates: tuple[FieldCandidate, ...] = field(repr=False)
    missing_fields: tuple[str, ...]
    facts: PageFacts = field(default_factory=PageFacts, repr=False)
    fields: tuple[ResolvedField, ...] = field(default=(), repr=False)


def resolve_fields(
    candidates: tuple[FieldCandidate, ...],
) -> tuple[ResolvedField, ...]:
    """Resolve bounded candidates while retaining every candidate as evidence."""
    if any(candidate.name not in DECLARED_FIELDS for candidate in candidates):
        raise ValueError("candidate uses an undeclared field")
    ranks = {
        "summary": 4,
        "attributes": 4,
        "page": 4,
        "description": 3,
        "email": 1,
    }
    resolved = []
    for name in DECLARED_FIELDS:
        options = [candidate for candidate in candidates if candidate.name == name]
        if not options:
            resolved.append(ResolvedField(name, FieldState.UNKNOWN))
            continue
        best = max(ranks.get(candidate.origin, 2) for candidate in options)
        selected = [
            candidate for candidate in options if ranks.get(candidate.origin, 2) == best
        ]
        if len({repr(candidate.value) for candidate in selected}) != 1:
            resolved.append(ResolvedField(name, FieldState.AMBIGUOUS))
        else:
            resolved.append(
                ResolvedField(
                    name,
                    FieldState.VALUE,
                    selected[0].value,
                    selected[0].origin,
                )
            )
    return tuple(resolved)


class Parser(Protocol):
    def parse(self, page: PageInput) -> ParserResult: ...
