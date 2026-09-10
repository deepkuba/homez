"""Reference capacity assumptions, not authorization for portal traffic."""

import pytest

from homefinder.parsers.contracts import Portal
from homefinder.scrape_queue.capacity import simulate_capacity


@pytest.mark.parametrize("source", ["gratka", "morizon", "otodom", "olx"])
def test_each_portal_completes_reference_daily_load(source: Portal) -> None:
    result = simulate_capacity(source)

    assert result.source == source
    assert result.daily_successes == 1000
    assert result.daily_last_completion_seconds < 86400
    assert result.slo_status == "available"


@pytest.mark.parametrize("response_seconds", [0, 1, 5, 10])
@pytest.mark.parametrize("source", ["gratka", "morizon", "otodom", "olx"])
def test_single_portal_burst_43rd_completion_meets_p95(
    source: Portal,
    response_seconds: int,
) -> None:
    result = simulate_capacity(source, response_seconds=response_seconds)

    assert len(result.burst_completions_seconds) == 45
    assert result.burst_p95_seconds == 420 + response_seconds
    assert result.burst_p95_seconds == result.burst_completions_seconds[42]
    assert result.burst_p95_seconds <= 600
    assert all(
        later - earlier >= 10
        for earlier, later in zip(
            result.burst_starts_seconds, result.burst_starts_seconds[1:], strict=False
        )
    )


def test_slower_policy_reports_unavailable_slo_without_changing_interval() -> None:
    result = simulate_capacity("olx", interval_seconds=15)

    assert result.daily_successes == 1000
    assert result.burst_p95_seconds == 640
    assert result.burst_starts_seconds[1] == 15
    assert result.slo_status == "unavailable"


@pytest.mark.parametrize(("attempt_limit", "success_limit"), [(150, 1000), (1000, 150)])
def test_source_ceiling_cannot_be_hidden_by_reference_capacity(
    attempt_limit: int, success_limit: int
) -> None:
    result = simulate_capacity(
        "morizon",
        daily_attempt_limit=attempt_limit,
        daily_success_limit=success_limit,
    )

    assert result.daily_successes == 150
    assert result.slo_status == "unavailable"


@pytest.mark.parametrize("interval_seconds", [0, -1, float("nan"), float("inf")])
def test_invalid_intervals_do_not_claim_capacity(interval_seconds: float) -> None:
    with pytest.raises(ValueError, match="capacity profile"):
        simulate_capacity("otodom", interval_seconds=interval_seconds)


def test_response_time_and_worker_occupancy_are_included() -> None:
    result = simulate_capacity("otodom", response_seconds=60)

    assert result.burst_starts_seconds[:3] == (0, 10, 60)
    assert result.burst_p95_seconds > 600
    assert result.slo_status == "unavailable"
