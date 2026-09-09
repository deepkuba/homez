"""Authenticated HTTP boundary for a single source-pinned scraper process."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from homefinder.scraper.rate_limit import (
    PortalRateLimiter,
    RateLimitPolicy,
    ScrapeDeferred,
)
from homefinder.sources.gmail import read_secret_text
from homefinder.sources.portal_pages import (
    PageFetchError,
    PageScrapeError,
    PortalPageScraper,
    ScrapedListing,
    supported_portals,
    validate_listing_url,
)


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


def create_scraper_app(
    source_key: str,
    *,
    token_file: Path,
    state_file: Path | None = None,
    rate_limit_policy: RateLimitPolicy | None = None,
    scrape: Callable[[str], ScrapedListing] | None = None,
) -> FastAPI:
    if source_key not in supported_portals():
        raise ValueError("unsupported scraper source")
    scraper = scrape or PortalPageScraper(source_key).scrape
    limiter = (
        PortalRateLimiter(state_file, policy=rate_limit_policy)
        if state_file is not None
        else None
    )
    scrape_lock = Lock()
    application = FastAPI(
        title=f"Homez {source_key} scraper",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "source": source_key}

    def locked_scrape(url: str) -> ScrapedListing:
        with scrape_lock:
            if limiter is not None:
                limiter.before_request()
            try:
                result = scraper(url)
            except PageFetchError as error:
                if limiter is not None and (
                    error.status_code in {403, 429} or error.status_code >= 500
                ):
                    raise limiter.after_failure(error) from error
                raise
            if limiter is not None:
                limiter.after_success()
            return result

    @application.post("/scrape")
    async def scrape_listing(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        _authorize(authorization, token_file)
        try:
            payload = ScrapeRequest.model_validate(await _bounded_json(request))
            canonical_url, _ = validate_listing_url(source_key, payload.url)
            result = await run_in_threadpool(locked_scrape, canonical_url)
            result_url, _ = validate_listing_url(source_key, result.canonical_url)
        except ValidationError as error:
            raise HTTPException(
                status_code=422, detail="request contract is invalid"
            ) from error
        except ScrapeDeferred as error:
            raise HTTPException(
                status_code=429,
                detail="portal request deferred",
                headers={"Retry-After": str(error.retry_after_seconds)},
            ) from error
        except PageScrapeError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=503, detail="scraper rate limiter unavailable"
            ) from error
        if result.source_key != source_key or result_url != canonical_url:
            raise HTTPException(
                status_code=502, detail="scraper result is inconsistent"
            )
        return result.as_json()

    return application


async def _bounded_json(request: Request) -> object:
    maximum = 4_096
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > maximum:
                raise HTTPException(status_code=413, detail="request body is too large")
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail="invalid content length"
            ) from error
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise HTTPException(status_code=413, detail="request body is too large")
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(
            status_code=422, detail="request body is invalid"
        ) from error


def _authorize(authorization: str | None, token_file: Path) -> None:
    prefix = "Bearer "
    presented = (
        authorization[len(prefix) :]
        if authorization is not None and authorization.startswith(prefix)
        else ""
    )
    try:
        expected = read_secret_text(token_file)
    except Exception as error:
        raise HTTPException(
            status_code=503, detail="scraper authentication unavailable"
        ) from error
    if not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=401,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
