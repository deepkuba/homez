"""Persistent per-process pacing and provider cooldowns for remote scrapers."""

from __future__ import annotations

import json
import math
import os
import secrets
import stat
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from random import SystemRandom

from homefinder.sources.portal_pages import PageFetchError


class ScrapeDeferred(RuntimeError):
    """The caller should retry after the bounded delay."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, min(retry_after_seconds, 86_400))
        super().__init__("portal request deferred")


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    minimum_interval_seconds: float = 15
    jitter_seconds: float = 5
    daily_limit: int = 150
    maximum_inline_wait_seconds: float = 20

    def __post_init__(self) -> None:
        if (
            self.minimum_interval_seconds <= 0
            or self.jitter_seconds < 0
            or self.daily_limit <= 0
            or self.maximum_inline_wait_seconds < 0
        ):
            raise ValueError("rate-limit settings are invalid")


@dataclass(slots=True)
class _State:
    day: str
    requests_today: int = 0
    next_request_at: str | None = None
    blocked_until: str | None = None
    consecutive_failures: int = 0


class PortalRateLimiter:
    """Rate limit one source; callers must serialize access to this object."""

    def __init__(
        self,
        state_file: Path,
        *,
        policy: RateLimitPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        self._state_file = state_file
        self._policy = policy or RateLimitPolicy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        random = SystemRandom()
        self._jitter = jitter or (lambda maximum: random.uniform(0, maximum))

    def before_request(self) -> None:
        now = _aware(self._clock())
        state = self._load(now.date())
        if state.day != now.date().isoformat():
            state.day = now.date().isoformat()
            state.requests_today = 0

        blocked_until = _timestamp(state.blocked_until)
        if blocked_until is not None and blocked_until > now:
            raise _deferred(blocked_until - now)
        if state.requests_today >= self._policy.daily_limit:
            tomorrow = datetime.combine(
                now.date() + timedelta(days=1), datetime.min.time(), timezone.utc
            )
            raise _deferred(tomorrow - now)

        next_request_at = _timestamp(state.next_request_at)
        if next_request_at is not None and next_request_at > now:
            wait = (next_request_at - now).total_seconds()
            if wait > self._policy.maximum_inline_wait_seconds:
                raise _deferred(next_request_at - now)
            self._sleep(wait)
            now = _aware(self._clock())

        interval = self._policy.minimum_interval_seconds + self._jitter(
            self._policy.jitter_seconds
        )
        state.requests_today += 1
        state.next_request_at = (now + timedelta(seconds=interval)).isoformat()
        self._save(state)

    def after_success(self) -> None:
        now = _aware(self._clock())
        state = self._load(now.date())
        state.consecutive_failures = 0
        state.blocked_until = None
        self._save(state)

    def after_failure(self, error: PageFetchError) -> ScrapeDeferred:
        now = _aware(self._clock())
        state = self._load(now.date())
        state.consecutive_failures += 1
        exponent = min(state.consecutive_failures - 1, 8)
        if error.status_code in {403, 429}:
            delay = error.retry_after_seconds or min(86_400, 21_600 * (2**exponent))
        else:
            delay = error.retry_after_seconds or min(21_600, 300 * (2**exponent))
        delay = max(1, min(delay, 86_400))
        state.blocked_until = (now + timedelta(seconds=delay)).isoformat()
        self._save(state)
        return ScrapeDeferred(delay)

    def _load(self, today: date) -> _State:
        try:
            metadata = self._state_file.lstat()
        except FileNotFoundError:
            return _State(day=today.isoformat())
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("scraper rate-limit state is not a regular file")
        try:
            raw = self._state_file.read_text(encoding="utf-8")
            payload = json.loads(raw)
            state = _State(**payload)
            _validate_state(state)
            return state
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("scraper rate-limit state is invalid") from error

    def _save(self, state: _State) -> None:
        parent = self._state_file.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = parent / f".{self._state_file.name}.{secrets.token_hex(8)}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(asdict(state), stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._state_file)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def _validate_state(state: _State) -> None:
    date.fromisoformat(state.day)
    if state.requests_today < 0 or state.consecutive_failures < 0:
        raise ValueError("negative limiter state")
    _timestamp(state.next_request_at)
    _timestamp(state.blocked_until)


def _timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("rate-limit timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _deferred(delay: timedelta) -> ScrapeDeferred:
    return ScrapeDeferred(math.ceil(delay.total_seconds()))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("rate-limit clock must be timezone-aware")
    return value.astimezone(timezone.utc)
