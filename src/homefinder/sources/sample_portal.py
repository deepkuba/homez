import hashlib
import json
from datetime import timezone
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
SOURCE_NAMESPACE = UUID("5fd65ed0-5954-4a57-8241-e0cf59df1200")
SOURCE = Source(
    id=uuid5(SOURCE_NAMESPACE, "sample_portal"),
    key="sample_portal",
    display_name="Sanitized sample portal",
)
EXPECTED_SENDER = "alerts@fixtures.homez.invalid"
EXPECTED_HOST = "listings.homez.invalid"


class _ListingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.listing_count = 0
        self.listing_id: str | None = None
        self.fields: dict[str, str] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self._field: str | None = None
        self._field_tag: str | None = None
        self._text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs_list: list[tuple[str, str | None]]
    ) -> None:
        attrs = {key: value for key, value in attrs_list}
        if tag == "article" and "data-homez-listing" in attrs:
            self.listing_count += 1
            self.listing_id = attrs.get("data-listing-id")
        field = attrs.get("data-field")
        if field is not None:
            self._field = field
            self._field_tag = tag
            self._text = []
            self.metadata[field] = {
                key: value for key, value in attrs.items() if value is not None
            }

    def handle_data(self, data: str) -> None:
        if self._field is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._field is not None and tag == self._field_tag:
            self.fields[self._field] = _normalize_space("".join(self._text))
            self._field = None
            self._field_tag = None
            self._text = []


class SamplePortalAlertParser:
    """Strict parser for the synthetic, sanitized alert contract used in Slice 1."""

    source_key = SOURCE.key
    version = "sample-v1"

    def parse(self, raw_message: bytes) -> ParsedAlert:
        if not raw_message or len(raw_message) > MAX_MESSAGE_BYTES:
            raise AlertParseError("message is empty or exceeds the size limit")

        parsed = BytesParser(policy=policy.default).parsebytes(raw_message)
        provider_message_id = _message_id(parsed)
        sender = parseaddr(parsed.get("From", ""))[1].lower()
        if sender != EXPECTED_SENDER:
            raise AlertParseError("unexpected sender for sample_portal")
        if parsed.get("X-Homez-Source") != SOURCE.key:
            raise AlertParseError("unsupported alert source")

        received_at = _received_at(parsed)
        body = parsed.get_body(preferencelist=("html",))
        if body is None:
            raise AlertParseError("HTML alert body is required")
        html = body.get_content()
        if not isinstance(html, str):
            raise AlertParseError("HTML alert body could not be decoded")

        extracted = _ListingHTMLParser()
        extracted.feed(html)
        required = {
            "title",
            "url",
            "price",
            "area",
            "rooms",
            "availability",
            "location",
        }
        if (
            extracted.listing_count != 1
            or not extracted.listing_id
            or not required.issubset(extracted.fields)
        ):
            raise AlertParseError("listing is missing required fields")

        canonical_url = extracted.metadata["url"].get("href", "")
        _validate_listing_url(canonical_url)
        try:
            price_minor = int(extracted.metadata["price"]["data-minor-units"])
            area_sqm = Decimal(extracted.metadata["area"]["data-square-metres"])
            rooms = int(extracted.metadata["rooms"]["data-count"])
            currency = extracted.metadata["price"]["data-currency"].upper()
            availability = extracted.metadata["availability"]["data-status"]
        except (KeyError, ValueError, InvalidOperation) as error:
            raise AlertParseError("listing contains invalid numeric data") from error
        if (
            price_minor <= 0
            or area_sqm <= 0
            or rooms <= 0
            or currency != "PLN"
            or availability not in {"available", "reserved", "unavailable"}
        ):
            raise AlertParseError("listing contains out-of-range values")

        listing = Listing(
            id=uuid4(),
            source_id=SOURCE.id,
            source_listing_id=extracted.listing_id,
            canonical_url=canonical_url,
            title=extracted.fields["title"],
        )
        description = extracted.fields.get("description", "")
        snapshot_values = {
            "price_minor": price_minor,
            "currency": currency,
            "area_sqm": str(area_sqm),
            "rooms": rooms,
            "availability": availability,
            "location": extracted.fields["location"],
            "description": description,
        }
        snapshot = ListingSnapshot(
            id=uuid4(),
            listing_id=listing.id,
            observed_at=received_at,
            price_minor=price_minor,
            currency=currency,
            area_sqm=area_sqm,
            rooms=rooms,
            availability=availability,
            location=extracted.fields["location"],
            description=description,
            content_hash=hashlib.sha256(
                json.dumps(snapshot_values, sort_keys=True).encode()
            ).hexdigest(),
        )
        return ParsedAlert(
            source=SOURCE,
            message=EmailMessage(
                id=uuid4(),
                source_id=SOURCE.id,
                provider_message_id=provider_message_id,
                received_at=received_at,
                sender=sender,
                subject=_normalize_space(str(parsed.get("Subject", ""))),
                raw_sha256=hashlib.sha256(raw_message).hexdigest(),
                parser_version=self.version,
            ),
            items=(ParsedListing(listing=listing, snapshot=snapshot),),
        )


def _normalize_space(value: str) -> str:
    return " ".join(value.split())


def _message_id(message: StdlibEmailMessage) -> str:
    value = message.get("Message-ID", "").strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    if not value or len(value) > 255 or any(character.isspace() for character in value):
        raise AlertParseError("valid Message-ID is required")
    return value


def _received_at(message: StdlibEmailMessage):  # type: ignore[no-untyped-def]
    try:
        value = parsedate_to_datetime(message.get("Date", ""))
    except (TypeError, ValueError) as error:
        raise AlertParseError("valid Date header is required") from error
    if value is None:
        raise AlertParseError("valid Date header is required")
    if value.tzinfo is None:
        raise AlertParseError("Date header must include a timezone")
    return value.astimezone(timezone.utc)


def _validate_listing_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != EXPECTED_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
    ):
        raise AlertParseError("listing URL is not allowlisted")
