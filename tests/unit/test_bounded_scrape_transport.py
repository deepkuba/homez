"""Manually authored in-memory bytes; no portal or proxy transport is constructed."""

import gzip
import zlib
from datetime import datetime, timezone

import pytest

from homefinder.scraper.contracts import FetchRequest

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
URL = "https://gratka.pl/nieruchomosci/synthetic/ob/10000001"


class Response:
    def __init__(self, body, *, encoding=None, status=200, retry_after=None):
        self.body = body
        self.encoding = encoding
        self.status = status
        self.closed = False
        self.read_sizes = []
        self.retry_after = retry_after

    def getheader(self, name):
        if name == "Content-Encoding":
            return self.encoding
        return self.retry_after if name == "Retry-After" else None

    def read(self, size):
        self.read_sizes.append(size)
        part, self.body = self.body[:size], self.body[size:]
        return part

    def close(self):
        self.closed = True


@pytest.mark.parametrize("encoding", [None, "identity", "gzip", "deflate"])
def test_transport_returns_exact_bounded_parser_bytes_and_closes(encoding):
    from homefinder.scraper.transport import BoundedPageTransport

    body = b"Synthetic parser bytes: \xff\xfe\x00" * 3000
    encoded = gzip.compress(body) if encoding == "gzip" else body
    if encoding == "deflate":
        encoded = zlib.compress(body)
    response = Response(encoded, encoding=encoding)
    calls = []

    def request(fetch_request, *, timeout_seconds):
        calls.append((fetch_request, timeout_seconds))
        return response

    transport = BoundedPageTransport(request=request, clock=lambda: NOW)
    page = transport.fetch(FetchRequest("gratka", URL + "?tracking=synthetic"))
    assert page.body == body and page.fetched_at == NOW
    assert page.transferred_bytes == len(encoded)
    assert calls == [(FetchRequest("gratka", URL), 10)]
    assert response.closed and max(response.read_sizes) <= 65_536


@pytest.mark.parametrize(
    ("body", "encoding", "status"),
    [
        (b"x" * 2_000_001, None, 200),
        (gzip.compress(b"x" * 2_000_001), "gzip", 200),
        (zlib.compress(b"x" * 2_000_001), "deflate", 200),
        (gzip.compress(b"synthetic")[:-1], "gzip", 200),
        (gzip.compress(b"one") + gzip.compress(b"two"), "gzip", 200),
        (zlib.compress(b"synthetic") + b"trailing", "deflate", 200),
        (b"synthetic", "br", 200),
        (b"synthetic", "gzip, identity", 200),
        (b"synthetic", "gzip", 200),
        (b"synthetic", None, 302),
        (b"synthetic", None, 403),
    ],
    ids=[
        "transfer-limit",
        "gzip-bomb",
        "deflate-bomb",
        "truncated",
        "concatenated",
        "trailing",
        "unsupported",
        "stacked",
        "invalid",
        "redirect",
        "denial",
    ],
)
def test_transport_rejects_unbounded_invalid_or_redirect_response(
    body, encoding, status
):
    from homefinder.scraper.transport import BoundedPageTransport, BoundedTransportError

    response = Response(body, encoding=encoding, status=status)
    transport = BoundedPageTransport(request=lambda *args, **kwargs: response)
    with pytest.raises(BoundedTransportError):
        transport.fetch(FetchRequest("gratka", URL))
    assert response.closed
    if status != 200:
        assert max(response.read_sizes) <= 65_536


def test_transport_rejects_foreign_url_before_request():
    from homefinder.scraper.transport import BoundedPageTransport, BoundedTransportError

    def request(*args, **kwargs):
        pytest.fail("invalid URL must not reach injected transport")

    with pytest.raises(BoundedTransportError):
        BoundedPageTransport(request=request).fetch(
            FetchRequest("gratka", "https://example.invalid/synthetic")
        )


def test_denial_reports_bounded_bytes_and_retry_after():
    from homefinder.scraper.transport import BoundedPageTransport, BoundedTransportError

    response = Response(b"synthetic denial", status=429, retry_after="1200")
    with pytest.raises(BoundedTransportError) as caught:
        BoundedPageTransport(request=lambda *args, **kwargs: response).fetch(
            FetchRequest("gratka", URL, "opaque-route-a")
        )

    assert caught.value.transferred_bytes == len(b"synthetic denial")
    assert caught.value.evidence.retry_after_seconds == 1200
    assert response.closed


def test_transport_accepts_exact_limit_with_bounded_decompression():
    from homefinder.scraper.transport import BoundedPageTransport

    response = Response(gzip.compress(b"x" * 2_000_000), encoding="gzip")
    result = BoundedPageTransport(request=lambda *args, **kwargs: response).fetch(
        FetchRequest("gratka", URL)
    )
    assert len(result.body) == 2_000_000 and response.closed


def test_transport_timeout_closes_response_without_exposing_response_content():
    import traceback

    from homefinder.scraper.transport import BoundedPageTransport, BoundedTransportError

    response = Response(b"synthetic")
    ticks = iter([0.0, 11.0])
    with pytest.raises(BoundedTransportError) as error:
        BoundedPageTransport(
            request=lambda *args, **kwargs: response, monotonic=lambda: next(ticks)
        ).fetch(FetchRequest("gratka", URL))
    assert response.closed
    assert "synthetic" not in "".join(traceback.format_exception(error.value))
