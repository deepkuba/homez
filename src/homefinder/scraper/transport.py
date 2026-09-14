"""Injected bounded HTTP capture; packaged network execution remains disabled."""

import math
import time
import zlib
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from homefinder.parsers.contracts import MAX_PAGE_BYTES, PageInput
from homefinder.scraper.contracts import (
    BoundedTransportError,
    CapturedPage,
    FetchRequest,
)
from homefinder.sources.portal_pages import validate_listing_url

CHUNK_BYTES = 65_536


class NetworkNotReleased(RuntimeError):
    """No centrally authorized network attempt is available."""


class DisabledPageTransport:
    def fetch(self, request: FetchRequest) -> PageInput:
        raise NetworkNotReleased("central network allocation is not released")


class BoundedResponse(Protocol):
    @property
    def status(self) -> int: ...

    def getheader(self, name: str) -> str | None: ...

    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


class ResponseRequest(Protocol):
    def __call__(
        self, request: FetchRequest, *, timeout_seconds: float
    ) -> BoundedResponse: ...


class BoundedPageTransport:
    """No default connector: injected requests must apply timeout and not redirect."""

    def __init__(
        self,
        *,
        request: ResponseRequest,
        timeout_seconds: float = 10,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 10:
            raise ValueError("capture timeout must be positive and at most 10 seconds")
        self._request = request
        self._timeout = timeout_seconds
        self._clock = clock
        self._monotonic = monotonic

    def fetch(self, request: FetchRequest) -> CapturedPage:
        response = None
        try:
            canonical, _ = validate_listing_url(request.source, request.canonical_url)
            deadline = self._monotonic() + self._timeout
            response = self._request(
                replace(request, canonical_url=canonical), timeout_seconds=self._timeout
            )
            if response.status != 200:
                raise BoundedTransportError(
                    "capture response rejected", status_code=response.status
                )
            encoding = (response.getheader("Content-Encoding") or "identity").lower()
            if encoding not in {"identity", "gzip", "deflate"}:
                raise BoundedTransportError("capture encoding unsupported")
            decoder = (
                zlib.decompressobj(
                    16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
                )
                if encoding != "identity"
                else None
            )
            body = bytearray()
            transferred = 0
            while True:
                if self._monotonic() >= deadline:
                    raise BoundedTransportError("capture deadline exceeded")
                read_limit = min(CHUNK_BYTES, MAX_PAGE_BYTES - transferred + 1)
                chunk = response.read(read_limit)
                if self._monotonic() >= deadline:
                    raise BoundedTransportError("capture deadline exceeded")
                if not isinstance(chunk, bytes) or len(chunk) > read_limit:
                    raise BoundedTransportError("capture stream contract invalid")
                if not chunk:
                    break
                transferred += len(chunk)
                if transferred > MAX_PAGE_BYTES:
                    raise BoundedTransportError("capture transfer exceeds 2 MB")
                if decoder is None:
                    body.extend(chunk)
                else:
                    body.extend(
                        decoder.decompress(chunk, MAX_PAGE_BYTES - len(body) + 1)
                    )
                    if decoder.unused_data or decoder.unconsumed_tail:
                        raise BoundedTransportError("capture compressed stream invalid")
                if len(body) > MAX_PAGE_BYTES:
                    raise BoundedTransportError("capture parser input exceeds 2 MB")
            if decoder is not None and not decoder.eof:
                raise BoundedTransportError("capture compressed stream incomplete")
            if not body:
                raise BoundedTransportError("capture parser input empty")
            return CapturedPage(
                PageInput(uuid4(), self._clock(), bytes(body)), transferred
            )
        except BoundedTransportError:
            raise
        except Exception:
            raise BoundedTransportError("capture transport failed") from None
        finally:
            if response is not None:
                with suppress(Exception):
                    response.close()
