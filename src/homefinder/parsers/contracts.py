"""Inert typed parser boundary; no transport or persistence dependencies."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

Portal = Literal["gratka", "morizon", "otodom", "olx"]
MAX_PAGE_BYTES = 2_000_000


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


@dataclass(frozen=True)
class ParserResult:
    capture_id: UUID
    release_hash: str
    variant: str
    candidates: tuple[FieldCandidate, ...] = field(repr=False)
    missing_fields: tuple[str, ...]


class Parser(Protocol):
    def parse(self, page: PageInput) -> ParserResult: ...
