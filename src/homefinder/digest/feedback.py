"""Single-use, scoped feedback authorization with audit events."""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from urllib.parse import quote, urlsplit
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import FeedbackEventRecord, FeedbackTokenRecord


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


class TokenStatus(str, Enum):
    VALID = "valid"
    EXPIRED = "expired"
    USED = "used"
    INVALID = "invalid"
    INVALID_SCOPE = "invalid_scope"


class SqlAlchemyFeedbackService:
    """Atomic single-use feedback backed by the application database."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        max_events_per_minute: int = 10,
    ) -> None:
        self._sessions = sessions
        self._max_events = max_events_per_minute

    def issue(
        self,
        report_id: str,
        listing_id: str,
        *,
        now: datetime,
        ttl: timedelta,
    ) -> str:
        raw = secrets.token_urlsafe(32)
        digest = _digest(raw)
        with self._sessions() as session:
            session.add(
                FeedbackTokenRecord(
                    token_hash=digest,
                    report_id=report_id,
                    listing_id=listing_id,
                    scope="feedback",
                    issued_at=now,
                    expires_at=now + ttl,
                )
            )
            session.commit()
        return raw

    def issue_stable(
        self,
        report_id: str,
        listing_id: str,
        *,
        now: datetime,
        ttl: timedelta,
        signing_key: bytes,
    ) -> str:
        """Issue a retry-stable capability while retaining only its hash."""
        if len(signing_key) < 32:
            raise ValueError("feedback signing key must contain at least 32 bytes")
        with self._sessions() as session:
            existing = session.scalar(
                select(FeedbackTokenRecord).where(
                    FeedbackTokenRecord.report_id == report_id,
                    FeedbackTokenRecord.listing_id == listing_id,
                    FeedbackTokenRecord.scope == "feedback",
                )
            )
            expires_at = existing.expires_at if existing is not None else now + ttl
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            raw = _stable_token(signing_key, report_id, listing_id, expires_at)
            if existing is not None:
                if not hmac.compare_digest(existing.token_hash, _digest(raw)):
                    raise FeedbackError("feedback signing key does not match token")
                return raw
            session.add(
                FeedbackTokenRecord(
                    token_hash=_digest(raw),
                    report_id=report_id,
                    listing_id=listing_id,
                    scope="feedback",
                    issued_at=now,
                    expires_at=expires_at,
                )
            )
            session.commit()
        return raw

    def inspect(
        self,
        raw: str,
        *,
        report_id: str,
        listing_id: str,
        now: datetime,
    ) -> TokenStatus:
        with self._sessions() as session:
            token = session.get(FeedbackTokenRecord, _digest(raw))
            if token is None or token.scope != "feedback":
                return TokenStatus.INVALID
            if token.report_id != report_id or token.listing_id != listing_id:
                return TokenStatus.INVALID_SCOPE
            if token.used_at is not None:
                return TokenStatus.USED
            expires_at = (
                token.expires_at
                if token.expires_at.tzinfo is not None
                else token.expires_at.replace(tzinfo=timezone.utc)
            )
            if expires_at <= now:
                return TokenStatus.EXPIRED
            return TokenStatus.VALID

    def record(
        self,
        *,
        method: str,
        token: str,
        csrf_token: str,
        expected_csrf: str,
        value: str,
        now: datetime,
        report_id: str,
        listing_id: str,
        actor_hash: str = "anonymous",
    ) -> FeedbackEvent:
        if method.upper() != "POST":
            raise FeedbackError("feedback requires POST")
        if not csrf_token or not hmac.compare_digest(csrf_token, expected_csrf):
            raise FeedbackError("invalid CSRF token")
        if value not in {"like", "dislike", "save"}:
            raise FeedbackError("invalid feedback value")
        digest = _digest(token)
        with self._sessions() as session:
            recent = session.scalar(
                select(func.count(FeedbackEventRecord.id)).where(
                    FeedbackEventRecord.actor_hash == actor_hash,
                    FeedbackEventRecord.recorded_at > now - timedelta(minutes=1),
                )
            )
            if int(recent or 0) >= self._max_events:
                raise FeedbackError("feedback rate limit exceeded")
            result = session.execute(
                update(FeedbackTokenRecord)
                .where(
                    FeedbackTokenRecord.token_hash == digest,
                    FeedbackTokenRecord.report_id == report_id,
                    FeedbackTokenRecord.listing_id == listing_id,
                    FeedbackTokenRecord.scope == "feedback",
                    FeedbackTokenRecord.used_at.is_(None),
                    FeedbackTokenRecord.expires_at > now,
                )
                .values(used_at=now)
            )
            if getattr(result, "rowcount", 0) != 1:
                session.rollback()
                status = self.inspect(
                    token,
                    report_id=report_id,
                    listing_id=listing_id,
                    now=now,
                )
                messages = {
                    TokenStatus.USED: "feedback token already used",
                    TokenStatus.EXPIRED: "feedback token expired",
                    TokenStatus.INVALID_SCOPE: "feedback token is out of scope",
                    TokenStatus.INVALID: "invalid feedback token",
                }
                raise FeedbackError(messages.get(status, "invalid feedback token"))
            session.add(
                FeedbackEventRecord(
                    id=uuid4(),
                    token_hash=digest,
                    report_id=report_id,
                    listing_id=listing_id,
                    value=value,
                    actor_hash=actor_hash,
                    recorded_at=now,
                )
            )
            session.commit()
        return FeedbackEvent(report_id, listing_id, value, now)


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _stable_token(
    signing_key: bytes,
    report_id: str,
    listing_id: str,
    expires_at: datetime,
) -> str:
    message = f"{report_id}\0{listing_id}\0{expires_at.isoformat()}".encode()
    value = hmac.digest(signing_key, message, "sha256")
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def private_feedback_url(
    base_url: str, *, report_id: str, listing_id: str, token: str
) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("feedback base URL must be a clean HTTPS origin")
    root = base_url.rstrip("/")
    return (
        f"{root}/feedback/{quote(report_id, safe='')}/"
        f"{quote(listing_id, safe='')}#{quote(token, safe='')}"
    )
