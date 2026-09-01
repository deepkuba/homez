"""Persistent, process-safe safety ledger for billable routing units."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import RoutingQuotaLedgerRecord


class QuotaExhausted(RuntimeError):
    """No route call may be made under the current quota state."""


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    reserved: int
    remaining: int
    provider_blocked: bool
    safety_ceiling: int


class QuotaLedger:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        period: str,
        provider: str,
        billable_unit: str,
        allowance: int,
        safety_ratio: float = 0.9,
        alert: Callable[[str], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if allowance < 0 or not 0 < safety_ratio <= 1:
            raise ValueError(
                "allowance must be non-negative and safety ratio in (0, 1]"
            )
        self._sessions = sessions
        self._key = (period, provider, billable_unit)
        self._ceiling = int(allowance * safety_ratio)
        self._allowance = allowance
        self._alert = alert
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ensure_record()

    def _ensure_record(self) -> None:
        with self._sessions() as session:
            if session.get(RoutingQuotaLedgerRecord, self._key) is not None:
                return
            session.add(
                RoutingQuotaLedgerRecord(
                    period=self._key[0],
                    provider=self._key[1],
                    billable_unit=self._key[2],
                    allowance=self._allowance,
                    safety_ceiling=self._ceiling,
                    reserved_units=0,
                    provider_blocked=False,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()

    def reserve(self, units: int = 1) -> None:
        if units <= 0:
            raise ValueError("units must be positive")
        period, provider, billable_unit = self._key
        with self._sessions() as session:
            result = session.execute(
                update(RoutingQuotaLedgerRecord)
                .where(
                    RoutingQuotaLedgerRecord.period == period,
                    RoutingQuotaLedgerRecord.provider == provider,
                    RoutingQuotaLedgerRecord.billable_unit == billable_unit,
                    RoutingQuotaLedgerRecord.provider_blocked.is_(False),
                    RoutingQuotaLedgerRecord.reserved_units + units
                    <= RoutingQuotaLedgerRecord.safety_ceiling,
                )
                .values(reserved_units=RoutingQuotaLedgerRecord.reserved_units + units)
            )
            if getattr(result, "rowcount", 0) == 1:
                session.commit()
                return
            session.rollback()
        snapshot = self.snapshot()
        if snapshot.provider_blocked:
            self._notify_once("routing provider quota exhausted")
            raise QuotaExhausted("provider quota exhausted")
        self._notify_once("local routing safety ceiling exhausted")
        raise QuotaExhausted("local routing safety ceiling exhausted")

    def trip_provider_quota(self, provider: str | None = None) -> None:
        period, provider_key, billable_unit = self._key
        with self._sessions() as session:
            session.execute(
                update(RoutingQuotaLedgerRecord)
                .where(
                    RoutingQuotaLedgerRecord.period == period,
                    RoutingQuotaLedgerRecord.provider == provider_key,
                    RoutingQuotaLedgerRecord.billable_unit == billable_unit,
                )
                .values(provider_blocked=True)
            )
            session.commit()
        self._notify_once(
            f"routing provider quota exhausted: {provider or provider_key}"
        )

    def reset_provider_quota(self) -> None:
        period, provider, billable_unit = self._key
        with self._sessions() as session:
            session.execute(
                update(RoutingQuotaLedgerRecord)
                .where(
                    RoutingQuotaLedgerRecord.period == period,
                    RoutingQuotaLedgerRecord.provider == provider,
                    RoutingQuotaLedgerRecord.billable_unit == billable_unit,
                )
                .values(provider_blocked=False, last_alert_at=None)
            )
            session.commit()

    def snapshot(self) -> QuotaSnapshot:
        with self._sessions() as session:
            record = session.get(RoutingQuotaLedgerRecord, self._key)
            if record is None:
                raise RuntimeError("routing quota ledger is unavailable")
            return QuotaSnapshot(
                reserved=record.reserved_units,
                remaining=max(0, record.safety_ceiling - record.reserved_units),
                provider_blocked=record.provider_blocked,
                safety_ceiling=record.safety_ceiling,
            )

    @property
    def reserved(self) -> int:
        return self.snapshot().reserved

    @property
    def remaining(self) -> int:
        return self.snapshot().remaining

    def _notify_once(self, reason: str) -> None:
        period, provider, billable_unit = self._key
        now = self._clock()
        with self._sessions() as session:
            result = session.execute(
                update(RoutingQuotaLedgerRecord)
                .where(
                    RoutingQuotaLedgerRecord.period == period,
                    RoutingQuotaLedgerRecord.provider == provider,
                    RoutingQuotaLedgerRecord.billable_unit == billable_unit,
                    RoutingQuotaLedgerRecord.last_alert_at.is_(None),
                )
                .values(last_alert_at=now)
            )
            session.commit()
        if getattr(result, "rowcount", 0) == 1 and self._alert is not None:
            self._alert(f"{period}: {reason}; reserved={self.snapshot().reserved}")
