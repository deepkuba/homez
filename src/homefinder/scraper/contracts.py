"""Bounded transport contract; implementations must not persist response bytes."""

from dataclasses import dataclass, field
from typing import Protocol

from homefinder.parsers.contracts import PageInput, Portal


@dataclass(frozen=True)
class FetchRequest:
    source: Portal
    canonical_url: str = field(repr=False)
    route_id: str | None = None


class PageTransport(Protocol):
    def fetch(self, request: FetchRequest) -> PageInput: ...
