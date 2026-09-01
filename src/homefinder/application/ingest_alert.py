from dataclasses import dataclass

from homefinder.catalog.repository import (
    ImmutableMessageConflictError,
    SqlAlchemyCatalogRepository,
)
from homefinder.preview import render_preview_card
from homefinder.sources.errors import AlertParseError
from homefinder.sources.protocols import AlertParser


@dataclass(frozen=True, slots=True)
class IngestionResult:
    created: bool
    preview_html: str
    preview_htmls: tuple[str, ...]


class AlertIngestionService:
    def __init__(
        self, parser: AlertParser, catalog: SqlAlchemyCatalogRepository
    ) -> None:
        self._parser = parser
        self._catalog = catalog

    @property
    def source_key(self) -> str:
        return self._parser.source_key

    @property
    def parser_version(self) -> str:
        return self._parser.version

    @property
    def expected_sender(self) -> str:
        return self._parser.expected_sender

    @property
    def max_message_bytes(self) -> int:
        return self._parser.max_message_bytes

    @property
    def expected_host(self) -> str:
        return self._parser.expected_host

    def ingest(self, raw_message: bytes) -> IngestionResult:
        parsed = self._parser.parse(raw_message)
        try:
            stored = self._catalog.add_alert(parsed)
        except ImmutableMessageConflictError as error:
            raise AlertParseError(
                "immutable Message-ID was reused with different content",
                code="message-conflict",
            ) from error
        previews = tuple(
            render_preview_card(item.listing, item.snapshot) for item in stored.items
        )
        return IngestionResult(
            created=stored.created,
            preview_html=previews[0],
            preview_htmls=previews,
        )
