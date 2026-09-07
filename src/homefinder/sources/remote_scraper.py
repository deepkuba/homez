"""Client for source-isolated scraper processes running on the NAS."""

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


class RemotePortalScraper:
    def __init__(
        self,
        source_key: str,
        *,
        endpoint: str,
        token_file: Path,
        timeout_seconds: float = 20.0,
        requester: RemoteRequester | None = None,
    ) -> None:
        if source_key not in supported_portals():
            raise ValueError("unsupported scraper source")
        if timeout_seconds <= 0:
            raise ValueError("scraper timeout must be positive")
        self.source_key = source_key
        self.endpoint = _scrape_endpoint(endpoint)
        self.token_file = token_file
        self.timeout_seconds = timeout_seconds
        self._requester = requester or _post_scrape

    def scrape(self, url: str) -> ScrapedListing:
        canonical_url, listing_id = validate_listing_url(self.source_key, url)
        token = read_secret_text(self.token_file)
        try:
            raw = self._requester(
                self.endpoint,
                canonical_url,
                token,
                self.timeout_seconds,
                MAX_SCRAPER_RESPONSE_BYTES,
            )
            payload = json.loads(raw)
        except PageScrapeError:
            raise
        except Exception as error:
            raise PageScrapeError("NAS scraper request failed") from error
        if not isinstance(payload, Mapping):
            raise PageScrapeError("NAS scraper response is invalid")
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
            raise PageScrapeError("NAS scraper response is inconsistent")
        return result


def _scrape_endpoint(value: str) -> str:
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
    is_tailnet = _is_tailscale_ip(
        parsed.hostname
    ) or parsed.hostname.casefold().endswith(".ts.net")
    if not is_tailnet:
        raise ValueError("scraper endpoint must use a Tailscale address")
    if parsed.scheme == "http" and not _is_tailscale_ip(parsed.hostname):
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
        raise PageScrapeError("NAS scraper endpoint is invalid")
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
        if response.status != 200:
            raise PageScrapeError("NAS scraper returned an unexpected status")
        content_type = response.getheader("Content-Type", "").casefold()
        if not content_type.startswith("application/json"):
            raise PageScrapeError("NAS scraper returned an unexpected content type")
        response_body = response.read(max_bytes + 1)
    except (OSError, http.client.HTTPException) as error:
        raise PageScrapeError("NAS scraper request failed") from error
    finally:
        connection.close()
    if len(response_body) > max_bytes:
        raise PageScrapeError("NAS scraper response exceeds the size limit")
    return response_body
