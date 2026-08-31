"""Single-use, scoped feedback authorization with audit events."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta


class FeedbackError(ValueError):
    pass


@dataclass(slots=True)
class _Token:
    report_id: str
    listing_id: str
    digest: str
    expires_at: datetime
    used: bool = False


@dataclass(frozen=True, slots=True)
class FeedbackEvent:
    report_id: str
    listing_id: str
    value: str
    recorded_at: datetime


class TokenStore:
    def __init__(self) -> None:
        self._tokens: dict[str, _Token] = {}

    def issue(
        self, report_id: str, listing_id: str, *, now: datetime, ttl: timedelta
    ) -> str:
        raw = secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        self._tokens[digest] = _Token(report_id, listing_id, digest, now + ttl)
        return raw

    def consume(
        self, raw: str, *, report_id: str, listing_id: str, now: datetime
    ) -> None:
        digest = hashlib.sha256(raw.encode()).hexdigest()
        token = self._tokens.get(digest)
        if (
            token is None
            or not hmac.compare_digest(token.digest, digest)
            or token.expires_at <= now
        ):
            raise FeedbackError("invalid or expired feedback token")
        if token.used:
            raise FeedbackError("feedback token already used")
        if token.report_id != report_id or token.listing_id != listing_id:
            raise FeedbackError("feedback token is out of scope")
        token.used = True


class FeedbackService:
    def __init__(self, tokens: TokenStore, *, max_events_per_minute: int = 10) -> None:
        self.tokens = tokens
        self.max_events = max_events_per_minute
        self.events: list[FeedbackEvent] = []

    def record(
        self,
        *,
        method: str,
        token: str,
        csrf_token: str,
        expected_csrf: str,
        value: str,
        now: datetime,
        report_id: str = "report-1",
        listing_id: str = "one",
    ) -> FeedbackEvent:
        if method.upper() != "POST":
            raise FeedbackError("feedback requires POST")
        if not csrf_token or not hmac.compare_digest(csrf_token, expected_csrf):
            raise FeedbackError("invalid CSRF token")
        if value not in {"like", "dislike", "save"}:
            raise FeedbackError("invalid feedback value")
        recent = [
            event
            for event in self.events
            if (now - event.recorded_at).total_seconds() < 60
        ]
        if len(recent) >= self.max_events:
            raise FeedbackError("feedback rate limit exceeded")
        self.tokens.consume(token, report_id=report_id, listing_id=listing_id, now=now)
        event = FeedbackEvent(report_id, listing_id, value, now)
        self.events.append(event)
        return event
