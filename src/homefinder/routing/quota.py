"""Thread-safe local safety ledger for billable routing units."""

from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock


class QuotaExhausted(RuntimeError):
    """No route call may be made under the current local/provider quota state."""


class QuotaLedger:
    def __init__(
        self,
        allowance: int,
        safety_ratio: float = 0.9,
        *,
        alert: Callable[[str], None] | None = None,
    ) -> None:
        if allowance < 0 or not 0 < safety_ratio <= 1:
            raise ValueError(
                "allowance must be non-negative and safety ratio in (0, 1]"
            )
        self._ceiling = int(allowance * safety_ratio)
        self._reserved = 0
        self._provider_blocked = False
        self._alert = alert
        self._alerted = False
        self._lock = Lock()

    @property
    def reserved(self) -> int:
        with self._lock:
            return self._reserved

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self._ceiling - self._reserved)

    def reserve(self, units: int = 1) -> None:
        if units <= 0:
            raise ValueError("units must be positive")
        with self._lock:
            if self._provider_blocked:
                self._notify("routing provider quota exhausted")
                raise QuotaExhausted("provider quota exhausted")
            if self._reserved + units > self._ceiling:
                self._notify("local routing safety ceiling exhausted")
                raise QuotaExhausted("local routing safety ceiling exhausted")
            self._reserved += units

    def trip_provider_quota(self, provider: str) -> None:
        with self._lock:
            self._provider_blocked = True
            self._notify(f"routing provider quota exhausted: {provider}")

    def reset_provider_quota(self) -> None:
        with self._lock:
            self._provider_blocked = False
            self._alerted = False

    def _notify(self, reason: str) -> None:
        if self._alert is not None and not self._alerted:
            self._alerted = True
            period = datetime.now(timezone.utc).strftime("%Y-%m")
            self._alert(f"{period}: {reason}; reserved={self._reserved}")
