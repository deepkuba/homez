from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from homefinder.scraper.rate_limit import (
    PortalRateLimiter,
    RateLimitPolicy,
    ScrapeDeferred,
)
from homefinder.sources.portal_pages import PageFetchError


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


NOW = datetime(2026, 9, 7, 8, tzinfo=timezone.utc)


def _limiter(state_file: Path, clock: Clock, *, daily_limit: int = 150):
    return PortalRateLimiter(
        state_file,
        policy=RateLimitPolicy(
            minimum_interval_seconds=15,
            jitter_seconds=5,
            daily_limit=daily_limit,
            maximum_inline_wait_seconds=20,
        ),
        clock=clock,
        sleep=clock.sleep,
        jitter=lambda maximum: maximum,
    )


def test_limiter_spaces_requests_and_persists_state_across_restart(
    tmp_path: Path,
) -> None:
    clock = Clock(NOW)
    state_file = tmp_path / "olx" / "rate-limit.json"
    first = _limiter(state_file, clock)

    first.before_request()
    restarted = _limiter(state_file, clock)
    restarted.before_request()

    assert clock.now == NOW + timedelta(seconds=20)
    assert state_file.stat().st_mode & 0o777 == 0o600


def test_daily_limit_defers_until_next_utc_day(tmp_path: Path) -> None:
    clock = Clock(NOW)
    limiter = _limiter(tmp_path / "state.json", clock, daily_limit=1)
    limiter.before_request()

    with pytest.raises(ScrapeDeferred) as captured:
        limiter.before_request()

    assert captured.value.retry_after_seconds == 16 * 60 * 60


@pytest.mark.parametrize(
    ("status", "retry_after", "expected"),
    ((429, 7_200, 7_200), (403, None, 21_600), (503, None, 300)),
)
def test_provider_throttling_creates_persistent_cooldown(
    tmp_path: Path,
    status: int,
    retry_after: int | None,
    expected: int,
) -> None:
    clock = Clock(NOW)
    state_file = tmp_path / "state.json"
    limiter = _limiter(state_file, clock)
    limiter.before_request()

    deferred = limiter.after_failure(
        PageFetchError(status_code=status, retry_after_seconds=retry_after)
    )

    assert deferred.retry_after_seconds == expected
    with pytest.raises(ScrapeDeferred) as captured:
        _limiter(state_file, clock).before_request()
    assert captured.value.retry_after_seconds == expected


def test_success_resets_exponential_provider_backoff(tmp_path: Path) -> None:
    clock = Clock(NOW)
    limiter = _limiter(tmp_path / "state.json", clock)
    limiter.before_request()
    first = limiter.after_failure(PageFetchError(status_code=503))
    clock.now += timedelta(seconds=first.retry_after_seconds)
    limiter.before_request()
    second = limiter.after_failure(PageFetchError(status_code=503))
    assert second.retry_after_seconds == 600

    clock.now += timedelta(seconds=second.retry_after_seconds)
    limiter.before_request()
    limiter.after_success()
    clock.now += timedelta(seconds=20)
    limiter.before_request()

    assert (
        limiter.after_failure(PageFetchError(status_code=503)).retry_after_seconds
        == 300
    )


def test_invalid_or_non_regular_state_fails_closed(tmp_path: Path) -> None:
    clock = Clock(NOW)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="ascii")
    symlink = tmp_path / "state-link.json"
    symlink.symlink_to(invalid)

    with pytest.raises(RuntimeError, match="state is invalid"):
        _limiter(invalid, clock).before_request()
    with pytest.raises(RuntimeError, match="regular file"):
        _limiter(symlink, clock).before_request()
