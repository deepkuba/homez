"""Bounded transport contract; implementations must not persist response bytes."""

from dataclasses import dataclass, field
from typing import Protocol

from homefinder.parsers.contracts import PageInput, Portal
from homefinder.scrape_queue.contracts import CaptureOutcome, NetworkPermit, ScrapeLease


@dataclass(frozen=True)
class FetchRequest:
    source: Portal
    canonical_url: str = field(repr=False)
    route_id: str | None = None


class PageTransport(Protocol):
    def fetch(self, request: FetchRequest) -> PageInput: ...


class CoordinatorClient(Protocol):
    def register(self, releases: tuple[str, ...], healthy: bool = True) -> None: ...

    def claim(self) -> ScrapeLease | None: ...

    def reserve_start(self, lease: ScrapeLease) -> NetworkPermit: ...

    def heartbeat(self, lease: ScrapeLease) -> ScrapeLease: ...

    def complete(self, lease: ScrapeLease, outcome: CaptureOutcome) -> None: ...

    def fail(self, lease: ScrapeLease, code: str) -> None: ...
