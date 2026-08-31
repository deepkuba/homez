import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email import policy
from email.message import EmailMessage as StdlibEmailMessage
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

from homefinder.domain.models import (
    EmailMessage,
    Listing,
    ListingSnapshot,
    ParsedAlert,
    ParsedListing,
    Source,
)
from homefinder.sources.errors import AlertParseError

MAX_MESSAGE_BYTES = 512_000
PARSER_VERSION = "sanitized-email-v1"
SOURCE_NAMESPACE = UUID("1dc5983b-2035-4cc4-a8bd-b8d63a83a8dc")
OLX_BLOCKED_REASON = "fixture unavailable; blocked on human issues #17 and #18"
_REQUIRED_FIELDS = frozenset({"title", "url", "price", "area", "rooms", "location"})
_PRICE_PATTERN = re.compile(r"([0-9][0-9\s\u00a0]*)\s*(PLN|zł)", re.IGNORECASE)
_AREA_PATTERN = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*m(?:2|²)", re.IGNORECASE)
_ROOMS_PATTERN = re.compile(r"([0-9]+)")


@dataclass(slots=True)
class _Article:
    listing_id: str
    fields: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, dict[str, str]] = field(default_factory=dict)


class _PortalHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.articles: list[_Article] = []
        self._article: _Article | None = None
        self._field: str | None = None
        self._field_tag: str | None = None
        self._text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs_list: list[tuple[str, str | None]]
    ) -> None:
        attrs = {key: value for key, value in attrs_list}
        if tag == "article" and attrs.get("data-listing-id") is not None:
            if self._article is not None:
                raise AlertParseError(
                    "nested listing elements are not supported", code="format-drift"
                )
            self._article = _Article(attrs["data-listing-id"] or "")
            self.articles.append(self._article)
        field_name = attrs.get("data-field")
        if self._article is not None and field_name is not None:
            self._field = field_name
            self._field_tag = tag
            self._text = []
            self._article.metadata[field_name] = {
                key: value for key, value in attrs.items() if value is not None
            }

    def handle_data(self, data: str) -> None:
        if self._field is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if (
            self._article is not None
            and self._field is not None
            and tag == self._field_tag
        ):
            self._article.fields[self._field] = _normalize_space("".join(self._text))
            self._field = None
            self._field_tag = None
            self._text = []
        if tag == "article" and self._article is not None:
            self._article = None


class SanitizedPortalAlertParser:
    """Strict fixture-backed email contract; it performs no network access."""

    source_key: str
    display_name: str
    expected_sender = "alerts@example.com"
    expected_host = "example.com"
    version = PARSER_VERSION

    @property
    def source(self) -> Source:
        return Source(
            id=uuid5(SOURCE_NAMESPACE, self.source_key),
            key=self.source_key,
            display_name=self.display_name,
        )

    def parse(self, raw_message: bytes) -> ParsedAlert:
        if not raw_message or len(raw_message) > MAX_MESSAGE_BYTES:
            raise AlertParseError(
                "message is empty or exceeds the size limit", code="message-size"
            )
        try:
            message = BytesParser(policy=policy.default).parsebytes(raw_message)
            sender = parseaddr(message.get("From", ""))[1].casefold()
            source_identity = message.get("X-Homez-Source")
            subject = _normalize_space(str(message.get("Subject", "")))
        except (KeyError, TypeError, ValueError) as error:
            raise AlertParseError(
                "email headers are malformed", code="malformed-email"
            ) from error
        if message.defects:
            raise AlertParseError(
                "email structure is malformed", code="malformed-email"
            )
        if sender != self.expected_sender:
            raise AlertParseError(
                f"sender is not allowed for {self.source_key}",
                code="unexpected-sender",
            )
        if source_identity != self.source_key:
            raise AlertParseError(
                f"message does not identify {self.source_key}", code="source-identity"
            )
        if len(subject) > 500:
            raise AlertParseError(
                "message subject exceeds the contract limit", code="field-limits"
            )

        html_body = message.get_body(preferencelist=("html",))
        if html_body is None:
            raise AlertParseError("HTML alert body is required", code="required-fields")
        content = html_body.get_content()
        if not isinstance(content, str):
            raise AlertParseError(
                "HTML alert body could not be decoded", code="format-drift"
            )
        extracted = _PortalHTMLParser()
        try:
            extracted.feed(content)
        except AlertParseError:
            raise
        except Exception as error:
            raise AlertParseError(
                "HTML does not match the parser contract", code="format-drift"
            ) from error
        if not extracted.articles:
            raise AlertParseError("no listings were found", code="required-fields")

        received_at = _received_at(message)
        parsed_items = tuple(
            self._normalize_article(article, received_at)
            for article in extracted.articles
        )
        listing_ids = [item.listing.source_listing_id for item in parsed_items]
        if len(set(listing_ids)) != len(listing_ids):
            raise AlertParseError(
                "listing identifiers must be unique within a message",
                code="duplicate-listing",
            )
        source = self.source
        return ParsedAlert(
            source=source,
            message=EmailMessage(
                id=uuid4(),
                source_id=source.id,
                provider_message_id=_message_id(message),
                received_at=received_at,
                sender=sender,
                subject=subject,
                raw_sha256=hashlib.sha256(raw_message).hexdigest(),
                parser_version=self.version,
            ),
            items=parsed_items,
        )

    def _normalize_article(
        self, article: _Article, received_at: datetime
    ) -> ParsedListing:
        if (
            not article.listing_id
            or len(article.listing_id) > 255
            or not _REQUIRED_FIELDS.issubset(article.fields)
            or any(not article.fields[key] for key in _REQUIRED_FIELDS)
        ):
            raise AlertParseError(
                "listing is missing required fields", code="required-fields"
            )
        if len(article.fields["title"]) > 500 or len(article.fields["location"]) > 500:
            raise AlertParseError(
                "listing fields exceed the contract limits", code="field-limits"
            )
        canonical_url = article.metadata.get("url", {}).get("href", "")
        self._validate_listing_url(canonical_url)
        try:
            price_match = _PRICE_PATTERN.fullmatch(article.fields["price"])
            area_match = _AREA_PATTERN.fullmatch(article.fields["area"])
            rooms_match = _ROOMS_PATTERN.fullmatch(article.fields["rooms"])
            if price_match is None or area_match is None or rooms_match is None:
                raise ValueError
            price_major = int(
                price_match.group(1).replace(" ", "").replace("\u00a0", "")
            )
            area_sqm = Decimal(area_match.group(1).replace(",", "."))
            rooms = int(rooms_match.group(1))
        except (InvalidOperation, ValueError) as error:
            raise AlertParseError(
                "listing contains invalid numeric values", code="numeric-values"
            ) from error
        if (
            price_major <= 0
            or price_major > 20_000_000
            or area_sqm <= 0
            or area_sqm > 999_999
            or rooms <= 0
            or rooms > 100
        ):
            raise AlertParseError(
                "listing contains out-of-range numeric values", code="numeric-values"
            )

        listing = Listing(
            id=uuid4(),
            source_id=self.source.id,
            source_listing_id=article.listing_id,
            canonical_url=canonical_url,
            title=article.fields["title"],
        )
        snapshot_values = {
            "price_minor": price_major * 100,
            "currency": "PLN",
            "area_sqm": str(area_sqm),
            "rooms": rooms,
            "availability": "available",
            "location": article.fields["location"],
            "description": article.fields.get("description", ""),
        }
        snapshot = ListingSnapshot(
            id=uuid4(),
            listing_id=listing.id,
            observed_at=received_at,
            price_minor=price_major * 100,
            currency="PLN",
            area_sqm=area_sqm,
            rooms=rooms,
            availability="available",
            location=article.fields["location"],
            description=article.fields.get("description", ""),
            content_hash=hashlib.sha256(
                json.dumps(snapshot_values, sort_keys=True).encode()
            ).hexdigest(),
        )
        return ParsedListing(listing=listing, snapshot=snapshot)

    def _validate_listing_url(self, value: str) -> None:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise AlertParseError(
                "listing URL is not allowlisted", code="listing-url"
            ) from error
        if (
            parsed.scheme != "https"
            or parsed.hostname != self.expected_host
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or parsed.query
            or parsed.fragment
            or len(value) > 2048
        ):
            raise AlertParseError("listing URL is not allowlisted", code="listing-url")


class OtodomAlertParser(SanitizedPortalAlertParser):
    source_key = "otodom"
    display_name = "Otodom"


class MorizonAlertParser(SanitizedPortalAlertParser):
    source_key = "morizon"
    display_name = "Morizon"


class GratkaAlertParser(SanitizedPortalAlertParser):
    source_key = "gratka"
    display_name = "Gratka"


def _normalize_space(value: str) -> str:
    return " ".join(value.split())


def _message_id(message: StdlibEmailMessage) -> str:
    try:
        value = message.get("Message-ID", "").strip()
    except (KeyError, TypeError, ValueError) as error:
        raise AlertParseError(
            "valid Message-ID is required", code="message-id"
        ) from error
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    if not value or len(value) > 255 or any(character.isspace() for character in value):
        raise AlertParseError("valid Message-ID is required", code="message-id")
    return value


def _received_at(message: StdlibEmailMessage) -> datetime:
    try:
        value = parsedate_to_datetime(message.get("Date", ""))
    except (TypeError, ValueError) as error:
        raise AlertParseError(
            "valid Date header is required", code="message-date"
        ) from error
    if value is None or value.tzinfo is None:
        raise AlertParseError(
            "Date header must include a timezone", code="message-date"
        )
    return value.astimezone(timezone.utc)
