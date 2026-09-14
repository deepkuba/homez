"""Transactional aggregate source pacing and bounded proxy allocation."""

from datetime import date, datetime, timedelta, timezone
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    PortalParserActivationRecord,
    ProxyRouteHealthRecord,
    ProxySourceQuarantineRecord,
    ProxyUsageLedgerRecord,
    ScrapeAttemptRecord,
    ScraperWorkerRecord,
    ScrapeTaskRecord,
    SourceRuntimeStateRecord,
)
from homefinder.parsers.contracts import Portal
from homefinder.scrape_queue.contracts import (
    LostLease,
    NetworkPermit,
    ProxyBudgetSnapshot,
    ScrapeLease,
    SourceBudgetPolicy,
    SourceBudgetSnapshot,
    WorkerIdentity,
)
from homefinder.scraper.denial_policy import (
    ResponseClassification,
    RouteClass,
    RouteDecision,
    decide_route_outcome,
)

PROXY_USABLE_BYTES = 900_000_000
PROXY_RESERVED_BYTES = 100_000_000


class SourceBudgetRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        policies: dict[str, SourceBudgetPolicy],
        billing_cycle_anchor_day: int = 1,
    ) -> None:
        if not policies:
            raise ValueError("at least one source policy is required")
        self._sessions = sessions
        self._policies = dict(policies)
        if not 1 <= billing_cycle_anchor_day <= 28:
            raise ValueError("billing cycle anchor day must be between 1 and 28")
        self._billing_cycle_anchor_day = billing_cycle_anchor_day

    def reserve_start(
        self,
        worker: WorkerIdentity,
        lease: ScrapeLease,
        *,
        now: datetime,
        requested_proxy_bytes: int = 0,
    ) -> NetworkPermit:
        _require_aware(now)
        if not 0 <= requested_proxy_bytes <= 2_000_000:
            raise ValueError("invalid proxy-byte reservation")
        policy = self._policy(worker.source)
        with self._sessions.begin() as session:
            task, attempt = self._validate_lease(session, worker, lease, now)
            if attempt.route_class != "unassigned":
                return NetworkPermit(
                    True,
                    _aware(attempt.network_started_at or attempt.started_at),
                    attempt.route_class,
                    attempt.route_id,
                )
            state = self._locked_source_state(session, worker.source, policy, now)
            available_at = max(
                now,
                _aware(state.next_start_at) if state.next_start_at else now,
                _aware(state.cooldown_until) if state.cooldown_until else now,
                _aware(task.direct_fallback_available_at)
                if task.direct_fallback_pending
                and task.direct_fallback_available_at is not None
                else now,
            )
            if (
                available_at > now
                or state.attempt_count >= policy.daily_attempt_limit
                or state.success_count >= policy.daily_success_limit
            ):
                if available_at <= now:
                    available_at = _next_utc_day(now)
                return NetworkPermit(False, available_at)

            route_id = None
            proxy_bytes = 0
            if requested_proxy_bytes and not task.direct_fallback_pending:
                ledger = self._locked_proxy_ledger(session, now)
                routes = session.scalars(
                    select(ProxyRouteHealthRecord)
                    .where(ProxyRouteHealthRecord.healthy.is_(True))
                    .order_by(ProxyRouteHealthRecord.route_id)
                    .with_for_update()
                )
                route_id = next(
                    (
                        route.route_id
                        for route in routes
                        if (
                            (
                                quarantine := session.get(
                                    ProxySourceQuarantineRecord,
                                    (route.route_id, worker.source),
                                )
                            )
                            is None
                            or _aware(quarantine.until) <= now
                        )
                    ),
                    None,
                )
                if (
                    route_id is not None
                    and ledger.allocated_bytes + requested_proxy_bytes
                    <= PROXY_USABLE_BYTES
                ):
                    ledger.allocated_bytes += requested_proxy_bytes
                    proxy_bytes = requested_proxy_bytes
                else:
                    route_id = None

            state.attempt_count += 1
            state.next_start_at = now + policy.minimum_interval
            route_class = "proxy" if route_id is not None else "direct"
            attempt.route_class = route_class
            attempt.route_id = route_id
            attempt.proxy_reserved_bytes = proxy_bytes
            attempt.network_attempt_count = 1
            attempt.network_started_at = now
            if task.direct_fallback_pending:
                task.direct_fallback_pending = False
                task.direct_fallback_available_at = None
            return NetworkPermit(
                True,
                now,
                route_class,
                route_id,
            )

    def record_outcome(
        self,
        worker: WorkerIdentity,
        lease: ScrapeLease,
        permit: NetworkPermit,
        classification: ResponseClassification,
        *,
        now: datetime,
        transferred_bytes: int = 0,
        retry_after_seconds: int | None = None,
    ) -> RouteDecision:
        _require_aware(now)
        if not permit.granted or permit.route_class not in {"direct", "proxy"}:
            raise ValueError("outcome requires a granted network permit")
        route_class = cast(RouteClass, permit.route_class)
        if not 0 <= transferred_bytes <= 2_000_000:
            raise ValueError("invalid compressed response byte count")
        policy = self._policy(worker.source)
        with self._sessions.begin() as session:
            task, attempt = self._validate_lease(session, worker, lease, now)
            if (
                attempt.route_class != permit.route_class
                or attempt.route_id != permit.route_id
                or attempt.classification is not None
            ):
                raise LostLease("network outcome does not match its permit")
            state = self._locked_source_state(session, worker.source, policy, now)
            decision = decide_route_outcome(
                route_class,
                classification,
                now=now,
                consecutive_direct_denials=state.direct_denial_count,
                retry_after_seconds=retry_after_seconds,
            )
            attempt.classification = classification.value
            attempt.response_bytes = transferred_bytes
            if permit.route_class == "proxy":
                self._reconcile_proxy_reservation(
                    session, attempt, transferred_bytes=transferred_bytes, now=now
                )
            if classification is ResponseClassification.SUCCESS:
                if state.success_count >= state.attempt_count:
                    raise ValueError("success has no reserved attempt")
                state.success_count += 1
                if permit.route_class == "direct":
                    state.direct_denial_count = 0
            if decision.source_cooldown_until is not None:
                state.cooldown_until = max(
                    _aware(state.cooldown_until) if state.cooldown_until else now,
                    decision.source_cooldown_until,
                )
                state.direct_denial_count = decision.consecutive_direct_denials
            if permit.route_id is not None and decision.mark_proxy_unhealthy:
                route = session.get(ProxyRouteHealthRecord, permit.route_id)
                if route is not None:
                    route.healthy = False
                    route.updated_at = now
            if permit.route_id is not None and decision.quarantine_proxy_source:
                quarantine = session.get(
                    ProxySourceQuarantineRecord, (permit.route_id, worker.source)
                )
                until = decision.retry_direct_at or now + timedelta(minutes=15)
                if quarantine is None:
                    session.add(
                        ProxySourceQuarantineRecord(
                            route_id=permit.route_id,
                            source=worker.source,
                            until=until,
                        )
                    )
                else:
                    quarantine.until = max(_aware(quarantine.until), until)
            if decision.retry_direct_at is not None:
                task.direct_fallback_pending = True
                task.direct_fallback_available_at = decision.retry_direct_at
                task.state = "deferred"
                task.available_at = decision.retry_direct_at
                task.lease_owner = None
                task.lease_token = None
                task.lease_started_at = None
                task.lease_expires_at = None
                attempt.finished_at = now
                attempt.outcome = "deferred"
                attempt.code = "portal-denied"
            return decision

    def record_success(self, source: Portal, *, now: datetime) -> None:
        _require_aware(now)
        policy = self._policy(source)
        with self._sessions.begin() as session:
            state = self._locked_source_state(session, source, policy, now)
            if state.success_count >= state.attempt_count:
                raise ValueError("success has no reserved attempt")
            state.success_count += 1

    def register_proxy_route(self, *, route_id: str, now: datetime) -> None:
        _require_aware(now)
        if not route_id or len(route_id) > 64:
            raise ValueError("invalid opaque route identifier")
        with self._sessions.begin() as session:
            route = session.get(ProxyRouteHealthRecord, route_id)
            if route is None:
                session.add(
                    ProxyRouteHealthRecord(
                        route_id=route_id, healthy=True, updated_at=now
                    )
                )
            else:
                route.healthy = True
                route.updated_at = now

    def record_proxy_bytes(
        self,
        *,
        route_id: str,
        source: Portal,
        transferred_bytes: int,
        now: datetime,
    ) -> None:
        del source
        _require_aware(now)
        if not 0 <= transferred_bytes <= PROXY_USABLE_BYTES:
            raise ValueError("invalid proxy byte count")
        with self._sessions.begin() as session:
            if session.get(ProxyRouteHealthRecord, route_id) is None:
                raise ValueError("unknown proxy route")
            ledger = self._locked_proxy_ledger(session, now)
            if ledger.transferred_bytes + transferred_bytes > PROXY_USABLE_BYTES:
                raise ValueError("proxy usage exceeds usable allowance")
            ledger.transferred_bytes += transferred_bytes
            ledger.allocated_bytes = max(
                ledger.allocated_bytes, ledger.transferred_bytes
            )

    def snapshot(self, source: Portal, *, now: datetime) -> SourceBudgetSnapshot:
        _require_aware(now)
        policy = self._policy(source)
        with self._sessions.begin() as session:
            state = self._locked_source_state(session, source, policy, now)
            return SourceBudgetSnapshot(
                source,
                state.attempt_count,
                state.success_count,
                _aware(state.cooldown_until) if state.cooldown_until else None,
            )

    def proxy_snapshot(self, *, now: datetime) -> ProxyBudgetSnapshot:
        _require_aware(now)
        with self._sessions.begin() as session:
            ledger = self._locked_proxy_ledger(session, now)
            return ProxyBudgetSnapshot(
                allocated_bytes=ledger.allocated_bytes,
                transferred_bytes=ledger.transferred_bytes,
            )

    def _policy(self, source: str) -> SourceBudgetPolicy:
        try:
            return self._policies[source]
        except KeyError:
            raise ValueError("source budget policy is unavailable") from None

    def _locked_source_state(
        self,
        session: Session,
        source: Portal,
        policy: SourceBudgetPolicy,
        now: datetime,
    ) -> SourceRuntimeStateRecord:
        state = session.scalar(
            select(SourceRuntimeStateRecord)
            .where(SourceRuntimeStateRecord.source == source)
            .with_for_update()
        )
        if state is None:
            try:
                with session.begin_nested():
                    state = SourceRuntimeStateRecord(
                        source=source,
                        policy_version=policy.policy_version,
                        day_key=now.date().isoformat(),
                        attempt_count=0,
                        success_count=0,
                        direct_denial_count=0,
                    )
                    session.add(state)
                    session.flush()
            except IntegrityError:
                state = session.scalar(
                    select(SourceRuntimeStateRecord)
                    .where(SourceRuntimeStateRecord.source == source)
                    .with_for_update()
                )
        if state is None:
            raise RuntimeError("source state could not be initialized")
        if state.day_key != now.date().isoformat():
            state.day_key = now.date().isoformat()
            state.attempt_count = 0
            state.success_count = 0
        state.policy_version = policy.policy_version
        return state

    def _locked_proxy_ledger(
        self, session: Session, now: datetime
    ) -> ProxyUsageLedgerRecord:
        cycle = self._billing_cycle(now).isoformat()
        ledger = session.scalar(
            select(ProxyUsageLedgerRecord)
            .where(ProxyUsageLedgerRecord.billing_cycle == cycle)
            .with_for_update()
        )
        if ledger is None:
            try:
                with session.begin_nested():
                    ledger = ProxyUsageLedgerRecord(
                        billing_cycle=cycle, allocated_bytes=0, transferred_bytes=0
                    )
                    session.add(ledger)
                    session.flush()
            except IntegrityError:
                ledger = session.scalar(
                    select(ProxyUsageLedgerRecord)
                    .where(ProxyUsageLedgerRecord.billing_cycle == cycle)
                    .with_for_update()
                )
        if ledger is None:
            raise RuntimeError("proxy ledger could not be initialized")
        return ledger

    def _reconcile_proxy_reservation(
        self,
        session: Session,
        attempt: ScrapeAttemptRecord,
        *,
        transferred_bytes: int,
        now: datetime,
    ) -> None:
        ledger = self._locked_proxy_ledger(session, now)
        remaining = ledger.allocated_bytes - attempt.proxy_reserved_bytes
        if remaining + transferred_bytes > PROXY_USABLE_BYTES:
            raise ValueError("proxy usage exceeds usable allowance")
        ledger.transferred_bytes += transferred_bytes
        ledger.allocated_bytes = remaining + transferred_bytes
        attempt.proxy_reserved_bytes = 0

    def _billing_cycle(self, now: datetime) -> date:
        year, month = now.year, now.month
        if now.day < self._billing_cycle_anchor_day:
            month -= 1
            if month == 0:
                year -= 1
                month = 12
        return date(year, month, self._billing_cycle_anchor_day)

    @staticmethod
    def _validate_lease(
        session: Session,
        worker: WorkerIdentity,
        lease: ScrapeLease,
        now: datetime,
    ) -> tuple[ScrapeTaskRecord, ScrapeAttemptRecord]:
        task = session.scalar(
            select(ScrapeTaskRecord)
            .where(
                ScrapeTaskRecord.id == lease.task_id,
                ScrapeTaskRecord.source == worker.source,
                ScrapeTaskRecord.snapshot_id == lease.snapshot_id,
                ScrapeTaskRecord.lease_owner == worker.worker_id,
                ScrapeTaskRecord.lease_token == lease.lease_token,
                ScrapeTaskRecord.state == "running",
                ScrapeTaskRecord.lease_expires_at > now,
            )
            .with_for_update()
        )
        identity = session.get(ScraperWorkerRecord, worker.worker_id)
        activation = session.get(PortalParserActivationRecord, worker.source)
        attempt = session.scalar(
            select(ScrapeAttemptRecord)
            .where(ScrapeAttemptRecord.lease_token == lease.lease_token)
            .with_for_update()
        )
        if (
            task is None
            or attempt is None
            or identity is None
            or (identity.source, identity.deployment)
            != (worker.source, worker.deployment)
            or activation is None
            or (task.release_hash, task.activation_epoch)
            != (activation.release_hash, activation.activation_epoch)
            or (task.release_hash, task.activation_epoch)
            != (lease.release_hash, lease.activation_epoch)
        ):
            raise LostLease("network start lease is invalid")
        return task, attempt


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


def _next_utc_day(value: datetime) -> datetime:
    current = value.astimezone(timezone.utc)
    return (current + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
