"""Bounded transport contract; implementations must not persist response bytes."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from homefinder.parsers.contracts import PageInput, Portal
from homefinder.scrape_queue.contracts import CaptureOutcome, NetworkPermit, ScrapeLease
from homefinder.scraper.denial_policy import ResponseClassification, RouteDecision


@dataclass(frozen=True)
class FetchRequest:
    source: Portal
    canonical_url: str = field(repr=False)
    route_id: str | None = None


@dataclass(frozen=True)
class CapturedPage:
    page: PageInput = field(repr=False)
    transferred_bytes: int

    def __post_init__(self) -> None:
        if not 0 <= self.transferred_bytes <= 2_000_000:
            raise ValueError("invalid transferred byte count")

    @property
    def body(self) -> bytes:
        return self.page.body

    @property
    def capture_id(self) -> UUID:
        return self.page.capture_id

    @property
    def fetched_at(self) -> datetime:
        return self.page.fetched_at


class PageTransport(Protocol):
    def fetch(self, request: FetchRequest) -> CapturedPage | PageInput: ...


class CoordinatorClient(Protocol):
    def register(self, releases: tuple[str, ...], healthy: bool = True) -> None: ...

    def claim(self) -> ScrapeLease | None: ...

    def reserve_start(self, lease: ScrapeLease) -> NetworkPermit: ...

    def defer(self, lease: ScrapeLease, available_at: datetime, code: str) -> None: ...

    def record_network_outcome(
        self,
        lease: ScrapeLease,
        permit: NetworkPermit,
        classification: ResponseClassification,
        transferred_bytes: int,
        retry_after_seconds: int | None = None,
    ) -> RouteDecision: ...

    def heartbeat(self, lease: ScrapeLease) -> ScrapeLease: ...

    def complete(self, lease: ScrapeLease, outcome: CaptureOutcome) -> None: ...

    def fail(self, lease: ScrapeLease, code: str) -> None: ...
