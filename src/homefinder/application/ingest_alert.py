from dataclasses import dataclass

from homefinder.catalog.repository import SqlAlchemyCatalogRepository
from homefinder.preview import render_preview_card
from homefinder.sources.protocols import AlertParser


@dataclass(frozen=True, slots=True)
class IngestionResult:
    created: bool
    preview_html: str


class AlertIngestionService:
    def __init__(
        self, parser: AlertParser, catalog: SqlAlchemyCatalogRepository
    ) -> None:
        self._parser = parser
        self._catalog = catalog

    def ingest(self, raw_message: bytes) -> IngestionResult:
        parsed = self._parser.parse(raw_message)
        stored = self._catalog.add_alert(parsed)
        return IngestionResult(
            created=stored.created,
            preview_html=render_preview_card(stored.listing, stored.snapshot),
        )
