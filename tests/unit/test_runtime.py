from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

from homefinder.runtime import heartbeat_is_fresh, run_periodically


def test_periodic_runtime_writes_heartbeat_after_success(tmp_path: Path) -> None:
    heartbeat = tmp_path / "runtime.heartbeat"
    calls: list[datetime] = []
    now = datetime(2026, 9, 2, 8, tzinfo=timezone.utc)

    run_periodically(
        lambda at: calls.append(at),
        interval_seconds=5,
        heartbeat_file=heartbeat,
        stop=Event(),
        clock=lambda: now,
        max_iterations=1,
    )

    assert calls == [now]
    assert heartbeat_is_fresh(heartbeat, now=now, max_age=timedelta(seconds=10))


def test_failed_runtime_iteration_does_not_report_healthy(tmp_path: Path) -> None:
    heartbeat = tmp_path / "runtime.heartbeat"

    def fail(_at: datetime) -> None:
        raise RuntimeError("provider unavailable")

    run_periodically(
        fail,
        interval_seconds=5,
        heartbeat_file=heartbeat,
        stop=Event(),
        max_iterations=1,
    )

    assert not heartbeat.exists()
