"""Small interruptible runtime loop shared by container worker processes."""

from __future__ import annotations

import logging
import os
import signal
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

LOGGER = logging.getLogger("homefinder.runtime")


def install_stop_signals(stop: Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def run_periodically(
    action: Callable[[datetime], None],
    *,
    interval_seconds: float,
    heartbeat_file: Path,
    stop: Event,
    clock: Callable[[], datetime] | None = None,
    max_iterations: int | None = None,
) -> None:
    if interval_seconds <= 0:
        raise ValueError("runtime interval must be positive")
    if max_iterations is not None and max_iterations < 1:
        raise ValueError("max_iterations must be positive")
    current_time = clock or (lambda: datetime.now(timezone.utc))
    iterations = 0
    while not stop.is_set():
        now = current_time()
        try:
            action(now)
        except Exception:
            LOGGER.exception(
                "runtime iteration failed", extra={"error_code": "iteration-failed"}
            )
        else:
            write_heartbeat(heartbeat_file, now)
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        stop.wait(interval_seconds)


def write_heartbeat(path: Path, checked_at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        checked_at.astimezone(timezone.utc).isoformat(), encoding="ascii"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def heartbeat_is_fresh(path: Path, *, now: datetime, max_age: timedelta) -> bool:
    if max_age <= timedelta(0):
        raise ValueError("heartbeat max age must be positive")
    try:
        checked_at = datetime.fromisoformat(path.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError):
        return False
    if checked_at.tzinfo is None:
        return False
    age = now.astimezone(timezone.utc) - checked_at.astimezone(timezone.utc)
    return timedelta(0) <= age <= max_age
