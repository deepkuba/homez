import base64
import binascii
import hashlib
import http.client
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email import policy
from email.message import EmailMessage as StdlibEmailMessage
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit
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
MAX_LISTINGS_PER_MESSAGE = 200
MAX_HTML_NODES = 10_000
MAX_HTML_DEPTH = 100
PARSER_VERSION = "portal-email-v3"
SOURCE_NAMESPACE = UUID("1dc5983b-2035-4cc4-a8bd-b8d63a83a8dc")
MAX_TRACKING_URL_BYTES = 4096
MAX_REDIRECT_CACHE_ENTRIES = 1024
RedirectFetcher = Callable[[str, float], tuple[int, tuple[str, ...]]]
_REQUIRED_FIELDS = frozenset({"title", "url", "price", "area", "rooms", "location"})
_PRICE_PATTERN = re.compile(r"([0-9][0-9\s\u00a0]*)\s*(PLN|zł)", re.IGNORECASE)
_AREA_PATTERN = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*m(?:2|²)", re.IGNORECASE)
_ROOMS_PATTERN = re.compile(r"([0-9]+)")
_ROOMS_TEXT_PATTERN = re.compile(
    r"(?<!\d)([0-9]{1,3})\s*(?:pok(?:ój|oje|oi)|rooms?)\b", re.IGNORECASE
)
_CARD_TAGS = frozenset({"article", "li", "tr", "td", "div"})
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
    }
)
_LOCATION_MARKERS = frozenset(
    {"address", "district", "locality", "location", "place", "region"}
)
_CALL_TO_ACTIONS = frozenset(
    {
        "check listing",
        "open listing",
        "see listing",
        "view",
        "view listing",
        "zobacz",
        "zobacz ogłoszenie",
        "sprawdź",
    }
)


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


@dataclass(slots=True)
class _HTMLNode:
    tag: str
    attrs: dict[str, str]
    parent: "_HTMLNode | None" = None
    children: list["_HTMLNode"] = field(default_factory=list)
    text: list[str] = field(default_factory=list)

    def text_chunks(self) -> list[str]:
        chunks = list(self.text)
        for child in self.children:
            chunks.extend(child.text_chunks())
        return [_normalize_space(chunk) for chunk in chunks if _normalize_space(chunk)]


class _EmailHTMLTreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HTMLNode("document", {})
        self._stack = [self.root]
        self._node_count = 0

    def handle_starttag(
        self, tag: str, attrs_list: list[tuple[str, str | None]]
    ) -> None:
        self._node_count += 1
        if self._node_count > MAX_HTML_NODES or len(self._stack) > MAX_HTML_DEPTH:
            raise AlertParseError(
                "HTML exceeds parser complexity limits", code="format-drift"
            )
        node = _HTMLNode(
            tag=tag,
            attrs={key.casefold(): value or "" for key, value in attrs_list},
            parent=self._stack[-1],
        )
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(
        self, tag: str, attrs_list: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs_list)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        self._stack[-1].text.append(data)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return


class SanitizedPortalAlertParser:
    """Strict source policy with constrained tracking-link normalization."""

    source_key: str
    display_name: str
    default_sender = "alerts@example.com"
    default_host = "example.com"
    tracking_host: str | None = None
    tracking_kind: str | None = None
    real_listing_hosts: frozenset[str] = frozenset()
    real_listing_path: re.Pattern[str] | None = None
    version = PARSER_VERSION
    max_message_bytes = MAX_MESSAGE_BYTES

    def __init__(
        self,
        *,
        allowed_senders: frozenset[str] | None = None,
        allowed_hosts: frozenset[str] | None = None,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
        redirect_fetcher: RedirectFetcher | None = None,
        redirect_timeout_seconds: float = 5.0,
    ) -> None:
        self.allowed_senders = frozenset(
            value.casefold()
            for value in (allowed_senders or frozenset({self.default_sender}))
        )
        self.allowed_hosts = frozenset(
            value.casefold()
            for value in (allowed_hosts or frozenset({self.default_host}))
        )
        if (
            not self.allowed_senders
            or not self.allowed_hosts
            or max_message_bytes <= 0
            or redirect_timeout_seconds <= 0
        ):
            raise ValueError(
                "portal parser allowlists, size limit, and timeout must be valid"
            )
        self.max_message_bytes = max_message_bytes
        self._redirect_fetcher = redirect_fetcher or _head_redirect
        self._redirect_timeout_seconds = redirect_timeout_seconds
        self._redirect_cache: dict[str, str] = {}

    @property
    def expected_sender(self) -> str:
        return sorted(self.allowed_senders)[0]

    @expected_sender.setter
    def expected_sender(self, value: str) -> None:
        self.allowed_senders = frozenset({value.casefold()})

    @property
    def expected_host(self) -> str:
        return sorted(self.allowed_hosts)[0]

    @expected_host.setter
    def expected_host(self, value: str) -> None:
        self.allowed_hosts = frozenset({value.casefold()})

    @property
    def source(self) -> Source:
        return Source(
            id=uuid5(SOURCE_NAMESPACE, self.source_key),
            key=self.source_key,
            display_name=self.display_name,
        )

    def parse(self, raw_message: bytes) -> ParsedAlert:
        if not raw_message or len(raw_message) > self.max_message_bytes:
            raise AlertParseError(
                "message is empty or exceeds the size limit", code="message-size"
            )
        try:
            message = BytesParser(policy=policy.default).parsebytes(raw_message)
            from_headers = message.get_all("From", [])
            if len(from_headers) != 1:
                raise ValueError
            sender = parseaddr(from_headers[0])[1].casefold()
            subject = _normalize_space(str(message.get("Subject", "")))
        except (KeyError, TypeError, ValueError) as error:
            raise AlertParseError(
                "email headers are malformed", code="malformed-email"
            ) from error
        if message.defects:
            raise AlertParseError(
                "email structure is malformed", code="malformed-email"
            )
        if not sender or sender not in self.allowed_senders:
            raise AlertParseError(
                f"sender is not allowed for {self.source_key}",
                code="unexpected-sender",
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
        articles = extracted.articles or self._extract_generic_articles(content)
        if not articles:
            raise AlertParseError("no listings were found", code="required-fields")
        if len(articles) > MAX_LISTINGS_PER_MESSAGE:
            raise AlertParseError(
                "message contains too many listings", code="field-limits"
            )

        received_at = _received_at(message)
        parsed_items = tuple(
            self._normalize_article(article, received_at) for article in articles
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
        canonical_url = self._canonical_listing_url(
            article.metadata.get("url", {}).get("href", "")
        )
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

    def _canonical_listing_url(self, value: str) -> str:
        value = self._resolve_tracking_url(value)
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise AlertParseError(
                "listing URL is not allowlisted", code="listing-url"
            ) from error
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.hostname.casefold() not in self.allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or parsed.path in ("", "/")
            or len(value) > 2048
            or _contains_control_characters(value)
            or not self._is_listing_path(parsed.hostname.casefold(), parsed.path)
        ):
            raise AlertParseError("listing URL is not allowlisted", code="listing-url")
        return urlunsplit(("https", parsed.netloc.casefold(), parsed.path, "", ""))

    def _resolve_tracking_url(self, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise AlertParseError(
                "listing URL is not allowlisted", code="listing-url"
            ) from error
        if (
            self.tracking_host is None
            or parsed.hostname is None
            or parsed.hostname.casefold() != self.tracking_host
        ):
            return value
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or parsed.path in ("", "/")
            or parsed.fragment
            or len(value.encode("utf-8")) > MAX_TRACKING_URL_BYTES
            or _contains_control_characters(value)
        ):
            raise AlertParseError("listing URL is not allowlisted", code="listing-url")

        if self.tracking_kind == "base64-path":
            return _decode_tracking_path(parsed.path)
        if self.tracking_kind != "http-302":
            raise AlertParseError("listing URL is not allowlisted", code="listing-url")

        cache_key = hashlib.sha256(value.encode()).hexdigest()
        cached = self._redirect_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            status, locations = self._redirect_fetcher(
                value, self._redirect_timeout_seconds
            )
        except Exception as error:
            raise AlertParseError(
                "listing redirect could not be resolved", code="listing-url"
            ) from error
        if (
            status != 302
            or len(locations) != 1
            or not isinstance(locations[0], str)
            or not locations[0]
            or len(locations[0].encode("utf-8")) > 2048
        ):
            raise AlertParseError(
                "listing redirect response is invalid", code="listing-url"
            )
        destination = locations[0]
        if len(self._redirect_cache) >= MAX_REDIRECT_CACHE_ENTRIES:
            del self._redirect_cache[next(iter(self._redirect_cache))]
        self._redirect_cache[cache_key] = destination
        return destination

    def _is_listing_path(self, hostname: str, path: str) -> bool:
        if hostname not in self.real_listing_hosts:
            return True
        return self.real_listing_path is not None and bool(
            self.real_listing_path.fullmatch(path)
        )

    def _extract_generic_articles(self, content: str) -> list[_Article]:
        tree = _EmailHTMLTreeParser()
        try:
            tree.feed(content)
        except Exception as error:
            raise AlertParseError(
                "HTML does not match the parser contract", code="format-drift"
            ) from error

        articles: list[_Article] = []
        seen_urls: set[str] = set()
        for anchor in _walk_nodes(tree.root):
            if anchor.tag != "a" or not anchor.attrs.get("href"):
                continue
            if not _has_listing_metrics_context(anchor):
                continue
            try:
                canonical_url = self._canonical_listing_url(anchor.attrs["href"])
            except AlertParseError:
                continue
            if canonical_url in seen_urls:
                continue
            article = self._article_from_anchor(anchor, canonical_url)
            if article is None:
                continue
            seen_urls.add(canonical_url)
            articles.append(article)
        return articles

    def _article_from_anchor(
        self, anchor: _HTMLNode, canonical_url: str
    ) -> _Article | None:
        node = anchor.parent
        while node is not None and node.tag != "document":
            if node.tag in _CARD_TAGS:
                article = self._article_from_card(anchor, node, canonical_url)
                if article is not None:
                    return article
            node = node.parent
        return None

    def _article_from_card(
        self, anchor: _HTMLNode, card: _HTMLNode, canonical_url: str
    ) -> _Article | None:
        chunks = card.text_chunks()
        combined = " | ".join(chunks)
        price = _PRICE_PATTERN.search(combined)
        area = _AREA_PATTERN.search(combined)
        rooms = _ROOMS_TEXT_PATTERN.search(combined)
        if price is None or area is None or rooms is None:
            return None

        title = _generic_title(anchor, card)
        location = _generic_location(card, title)
        if not title or not location:
            return None
        url_digest = hashlib.sha256(canonical_url.encode()).hexdigest()[:32]
        listing_id = f"{self.source_key}-{url_digest}"
        return _Article(
            listing_id=listing_id,
            fields={
                "title": title,
                "url": canonical_url,
                "price": price.group(0),
                "area": area.group(0),
                "rooms": rooms.group(1),
                "location": location,
            },
            metadata={"url": {"href": canonical_url}},
        )


class OtodomAlertParser(SanitizedPortalAlertParser):
    source_key = "otodom"
    display_name = "Otodom"
    tracking_host = "clicks.alerts.otodom.pl"
    tracking_kind = "http-302"
    real_listing_hosts = frozenset({"otodom.pl", "www.otodom.pl"})
    real_listing_path = re.compile(r"/pl/oferta/[^/]+")


class MorizonAlertParser(SanitizedPortalAlertParser):
    source_key = "morizon"
    display_name = "Morizon"
    tracking_host = "link.morizon.pl"
    tracking_kind = "base64-path"
    real_listing_hosts = frozenset({"morizon.pl", "www.morizon.pl"})
    real_listing_path = re.compile(r"/oferta/[^/]+")


class GratkaAlertParser(SanitizedPortalAlertParser):
    source_key = "gratka"
    display_name = "Gratka"
    tracking_host = "link.gratka.pl"
    tracking_kind = "base64-path"
    real_listing_hosts = frozenset({"gratka.pl", "www.gratka.pl"})
    real_listing_path = re.compile(r"/nieruchomosci/.+/ob/[0-9]+/?")


class OLXAlertParser(SanitizedPortalAlertParser):
    source_key = "olx"
    display_name = "OLX"
    tracking_host = "clicks.marketing.olx.pl"
    tracking_kind = "http-302"
    real_listing_hosts = frozenset({"olx.pl", "www.olx.pl"})
    real_listing_path = re.compile(r"/d/oferta/[^/]+\.html")


def _decode_tracking_path(path: str) -> str:
    parts = path.split("/")
    if (
        len(parts) != 5
        or parts[0] != ""
        or parts[1] != "click"
        or not all(parts[2:])
        or len(parts[3]) > 3072
        or len(parts[3]) % 4 == 1
    ):
        raise AlertParseError("listing tracking URL is invalid", code="listing-url")
    encoded = parts[3]
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        decoded = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        ).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error) as error:
        raise AlertParseError(
            "listing tracking URL is invalid", code="listing-url"
        ) from error
    if not decoded or len(decoded.encode("utf-8")) > 2048:
        raise AlertParseError("listing tracking URL is invalid", code="listing-url")
    return decoded


def _head_redirect(url: str, timeout_seconds: float) -> tuple[int, tuple[str, ...]]:
    parsed = urlsplit(url)
    if parsed.hostname is None:
        raise ValueError("redirect URL has no host")
    target = urlunsplit(("", "", parsed.path, parsed.query, ""))
    connection = http.client.HTTPSConnection(
        parsed.hostname, port=parsed.port or 443, timeout=timeout_seconds
    )
    try:
        connection.request(
            "HEAD",
            target,
            headers={
                "Accept": "*/*",
                "User-Agent": "Homez-LinkResolver/1.0",
            },
        )
        response = connection.getresponse()
        try:
            locations = tuple(
                value
                for name, value in response.getheaders()
                if name.casefold() == "location"
            )
            return response.status, locations
        finally:
            response.close()
    finally:
        connection.close()


def _contains_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _walk_nodes(node: _HTMLNode) -> list[_HTMLNode]:
    nodes: list[_HTMLNode] = []
    pending = list(reversed(node.children))
    while pending:
        child = pending.pop()
        nodes.append(child)
        pending.extend(reversed(child.children))
    return nodes


def _has_listing_metrics_context(anchor: _HTMLNode) -> bool:
    node = anchor.parent
    while node is not None and node.tag != "document":
        if node.tag in _CARD_TAGS:
            combined = " | ".join(node.text_chunks())
            if (
                _PRICE_PATTERN.search(combined)
                and _AREA_PATTERN.search(combined)
                and _ROOMS_TEXT_PATTERN.search(combined)
            ):
                return True
        node = node.parent
    return False


def _generic_title(anchor: _HTMLNode, card: _HTMLNode) -> str:
    anchor_text = _normalize_space(" ".join(anchor.text_chunks()))
    if (
        2 < len(anchor_text) <= 500
        and anchor_text.casefold() not in _CALL_TO_ACTIONS
        and _PRICE_PATTERN.search(anchor_text) is None
    ):
        return anchor_text
    for node in _walk_nodes(card):
        if node.tag in {"h1", "h2", "h3", "h4"}:
            heading = _normalize_space(" ".join(node.text_chunks()))
            if 2 < len(heading) <= 500:
                return heading
    return ""


def _generic_location(card: _HTMLNode, title: str) -> str:
    for node in _walk_nodes(card):
        marker_text = " ".join(
            (node.attrs.get("class", ""), node.attrs.get("id", ""))
        ).casefold()
        markers = frozenset(re.findall(r"[a-z]+", marker_text))
        if markers & _LOCATION_MARKERS:
            location = _normalize_space(" ".join(node.text_chunks()))
            if 0 < len(location) <= 500:
                return location

    candidates: list[str] = []
    for chunk in card.text_chunks():
        folded = chunk.casefold()
        if (
            chunk == title
            or folded in _CALL_TO_ACTIONS
            or _PRICE_PATTERN.search(chunk)
            or _AREA_PATTERN.search(chunk)
            or _ROOMS_TEXT_PATTERN.search(chunk)
            or len(chunk) > 500
        ):
            continue
        candidates.append(chunk)
    return candidates[0] if len(candidates) == 1 else ""


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
