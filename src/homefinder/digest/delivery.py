"""Friday delivery timing and exactly-once period claims."""

from collections.abc import Callable
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo


class InMemoryDeliveryLedger:
    def __init__(self) -> None:
        self._sent: set[str] = set()
        self._lock = Lock()

    def claim(self, period: str) -> bool:
        with self._lock:
            if period in self._sent:
                return False
            self._sent.add(period)
            return True


class DigestDelivery:
    def __init__(self, ledger: InMemoryDeliveryLedger) -> None:
        self.ledger = ledger

    @staticmethod
    def is_due(at: datetime) -> bool:
        local = at.astimezone(ZoneInfo("Europe/Warsaw"))
        return local.weekday() == 4 and local.hour == 10

    def send_once(self, period: str, send: Callable[[], None]) -> bool:
        if not self.ledger.claim(period):
            return False
        try:
            send()
        except Exception:
            # A failed claim must be retryable; an acknowledged send remains unique.
            self.ledger._sent.discard(period)
            raise
        return True
