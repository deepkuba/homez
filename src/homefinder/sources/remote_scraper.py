"""Client for redundant source-isolated scraper processes."""

from __future__ import annotations

import http.client
import ipaddress
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from homefinder.sources.gmail import read_secret_text
from homefinder.sources.portal_pages import (
    PageScrapeError,
    ScrapedListing,
    supported_portals,
    validate_listing_url,
)

MAX_SCRAPER_RESPONSE_BYTES = 64_000
RemoteRequester = Callable[[str, str, str, float, int], bytes]


class RemoteScrapeDeferred(PageScrapeError):
    """A scraper asked the durable workflow to retry later."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, min(retry_after_seconds, 86_400))
        super().__init__("portal request deferred")


class RemoteScraperUnavailable(PageScrapeError):
    """A scraper service could not be reached or failed server-side."""


class RemotePortalScraper:
    def __init__(
        self,
        source_key: str,
        *,
        endpoint: str,
        fallback_endpoint: str | None = None,
        token_file: Path,
        timeout_seconds: float = 45.0,
        requester: RemoteRequester | None = None,
    ) -> None:
        if source_key not in supported_portals():
            raise ValueError("unsupported scraper source")
        if timeout_seconds <= 0:
            raise ValueError("scraper timeout must be positive")
        self.source_key = source_key
        self.endpoint = _scrape_endpoint(endpoint, source_key=source_key)
        self.fallback_endpoint = (
            _scrape_endpoint(fallback_endpoint, source_key=source_key)
            if fallback_endpoint is not None
            else None
        )
        self.token_file = token_file
        self.timeout_seconds = timeout_seconds
        self._requester = requester or _post_scrape

    def scrape(self, url: str) -> ScrapedListing:
        canonical_url, listing_id = validate_listing_url(self.source_key, url)
        token = read_secret_text(self.token_file)
        raw = self._request(canonical_url, token)
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise PageScrapeError("scraper response is invalid") from error
        if not isinstance(payload, Mapping):
            raise PageScrapeError("scraper response is invalid")
        result = ScrapedListing.from_json(payload)
        result_url, result_id = validate_listing_url(
            self.source_key, result.canonical_url
        )
        if (
            result.source_key != self.source_key
            or result_url != canonical_url
            or result_id.casefold() != listing_id.casefold()
            or result.source_listing_id.casefold() != listing_id.casefold()
        ):
            raise PageScrapeError("scraper response is inconsistent")
        return result

    def _request(self, canonical_url: str, token: str) -> bytes:
        endpoints = tuple(
            endpoint
            for endpoint in (self.endpoint, self.fallback_endpoint)
            if endpoint is not None
        )
        for position, endpoint in enumerate(endpoints):
            try:
                return self._requester(
                    endpoint,
                    canonical_url,
                    token,
                    self.timeout_seconds,
                    MAX_SCRAPER_RESPONSE_BYTES,
                )
            except RemoteScraperUnavailable:
                if position == len(endpoints) - 1:
                    raise
            except PageScrapeError:
                raise
            except Exception as error:
                unavailable = RemoteScraperUnavailable("scraper service request failed")
                if position == len(endpoints) - 1:
                    raise unavailable from error
        raise RemoteScraperUnavailable("no scraper service is available")


def _scrape_endpoint(value: str, *, source_key: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("scraper endpoint is invalid") from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("scraper endpoint is invalid")
    hostname = parsed.hostname.casefold()
    internal_hostname = f"scraper-vps-{source_key}"
    is_internal = hostname == internal_hostname
    is_tailnet = _is_tailscale_ip(hostname) or hostname.endswith(".ts.net")
    if not is_internal and not is_tailnet:
        raise ValueError(
            "scraper endpoint must use a Tailscale address or "
            "source-pinned private service"
        )
    if is_internal and (parsed.scheme != "http" or port not in {None, 8000}):
        raise ValueError("private scraper endpoint must use its internal HTTP port")
    if not is_internal and parsed.scheme == "http" and not _is_tailscale_ip(hostname):
        raise ValueError("cleartext scraper endpoint must use a Tailscale IP")
    authority = parsed.hostname
    if ":" in authority:
        authority = f"[{authority}]"
    if port is not None:
        authority = f"{authority}:{port}"
    return urlunsplit((parsed.scheme, authority, "/scrape", "", ""))


def _is_tailscale_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return isinstance(
        address, ipaddress.IPv4Address
    ) and address in ipaddress.ip_network("100.64.0.0/10")


def _post_scrape(
    endpoint: str,
    listing_url: str,
    token: str,
    timeout_seconds: float,
    max_bytes: int,
) -> bytes:
    parsed = urlsplit(endpoint)
    if parsed.hostname is None:
        raise PageScrapeError("scraper endpoint is invalid")
    connection_type = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(
        parsed.hostname, port=parsed.port, timeout=timeout_seconds
    )
    body = json.dumps({"url": listing_url}, separators=(",", ":")).encode()
    try:
        connection.request(
            "POST",
            parsed.path,
            body=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        response = connection.getresponse()
        if response.status == 429:
            raise RemoteScrapeDeferred(_retry_after(response.getheader("Retry-After")))
        if response.status >= 500:
            raise RemoteScraperUnavailable("scraper service is unavailable")
        if response.status != 200:
            raise PageScrapeError("scraper returned an unexpected status")
        content_type = response.getheader("Content-Type", "").casefold()
        if not content_type.startswith("application/json"):
            raise PageScrapeError("scraper returned an unexpected content type")
        response_body = response.read(max_bytes + 1)
    except (OSError, http.client.HTTPException) as error:
        raise RemoteScraperUnavailable("scraper service request failed") from error
    finally:
        connection.close()
    if len(response_body) > max_bytes:
        raise PageScrapeError("scraper response exceeds the size limit")
    return response_body


def _retry_after(value: str | None) -> int:
    try:
        parsed = int(value) if value is not None else 300
    except ValueError:
        parsed = 300
    return max(1, min(parsed, 86_400))
