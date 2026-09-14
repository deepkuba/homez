"""Source-pinned worker runtime; no ORM, database settings, or candidate lane."""

import argparse
import hashlib
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from typing import cast

from homefinder.artifacts.contracts import ArtifactWriter
from homefinder.parsers.contracts import Parser, ParserResult, Portal
from homefinder.runtime import install_stop_signals, write_heartbeat
from homefinder.scrape_queue.contracts import (
    CaptureOutcome,
    LostLease,
    TaskClass,
    WorkerIdentity,
)
from homefinder.scraper.contracts import (
    BoundedTransportError,
    CapturedPage,
    CoordinatorClient,
    FetchRequest,
    PageTransport,
)
from homefinder.scraper.denial_policy import (
    FailureEvidence,
    ResponseClassification,
    ResponseEvidence,
    RouteClass,
    classify_response,
)


class ScrapeWorker:
    def __init__(
        self,
        *,
        source: Portal,
        coordinator: CoordinatorClient,
        transport: PageTransport,
        parsers: Mapping[str, Parser],
        stop: Event,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        heartbeat_seconds: float = 20,
        idle_seconds: float = 2,
        heartbeat_file: Path | None = None,
        artifact_writer: ArtifactWriter | None = None,
    ) -> None:
        if source not in {"gratka", "morizon", "otodom", "olx"}:
            raise ValueError("invalid worker source")
        if not 0 < heartbeat_seconds < 60 or not 0 < idle_seconds <= 60:
            raise ValueError("invalid worker timing")
        self.source = source
        self.coordinator = coordinator
        self.transport = transport
        self.parsers = dict(parsers)
        self.stop = stop
        self.clock = clock
        self.heartbeat_seconds = heartbeat_seconds
        self.idle_seconds = idle_seconds
        self.heartbeat_file = heartbeat_file
        self.artifact_writer = artifact_writer

    def _advertise(self, healthy: bool = True) -> None:
        self.coordinator.register(tuple(sorted(self.parsers)), healthy=healthy)
        if healthy and self.heartbeat_file is not None:
            write_heartbeat(self.heartbeat_file, self.clock())

    def run_once(self) -> bool:
        if self.stop.is_set():
            return False
        self._advertise()
        # No packaged executable releases means no claim and no portal request.
        if not self.parsers:
            return False
        lease = self.coordinator.claim()
        if lease is None:
            return False
        if (
            (lease.lease_expires_at - self.clock()).total_seconds()
            <= self.heartbeat_seconds
            or lease.source != self.source
            or lease.release_hash not in self.parsers
            or lease.task_class not in {TaskClass.LIVE, TaskClass.NETWORK_RECOVERY}
        ):
            return True
        done, lost = Event(), Event()

        def renew() -> None:
            while not done.wait(self.heartbeat_seconds):
                try:
                    self.coordinator.heartbeat(lease)
                    self._advertise()
                except Exception:
                    lost.set()
                    return

        thread = Thread(target=renew, name="scrape-lease-heartbeat", daemon=True)
        thread.start()
        try:
            try:
                permit = self.coordinator.reserve_start(lease)
            except Exception:
                return True
            if not permit.granted:
                with suppress(Exception):
                    self.coordinator.defer(
                        lease, permit.available_at, "budget-exhausted"
                    )
                return True
            if permit.route_class not in {"direct", "proxy"}:
                return True
            route_class = cast(RouteClass, permit.route_class)
            try:
                capture = self.transport.fetch(
                    FetchRequest(self.source, lease.canonical_url, permit.route_id)
                )
            except BoundedTransportError as error:
                if lost.is_set():
                    return True
                try:
                    decision = self.coordinator.record_network_outcome(
                        lease,
                        permit,
                        classify_response(route_class, error.evidence),
                        error.transferred_bytes,
                        error.evidence.retry_after_seconds,
                    )
                except Exception:
                    return True
                if decision.retry_direct_at is not None:
                    return True
                if decision.source_cooldown_until is not None:
                    with suppress(Exception):
                        self.coordinator.defer(
                            lease,
                            decision.source_cooldown_until,
                            "source-cooldown",
                        )
                    return True
                with suppress(Exception):
                    self.coordinator.fail(lease, "transport-error")
                return True
            except Exception:
                if not lost.is_set():
                    with suppress(Exception):
                        evidence = ResponseEvidence(
                            failure=FailureEvidence.TRANSPORT_FAILURE
                        )
                        self.coordinator.record_network_outcome(
                            lease,
                            permit,
                            classify_response(route_class, evidence),
                            0,
                        )
                    with suppress(Exception):
                        self.coordinator.fail(lease, "transport-error")
                return True
            if lost.is_set():
                return True
            page = capture.page if isinstance(capture, CapturedPage) else capture
            transferred_bytes = (
                capture.transferred_bytes
                if isinstance(capture, CapturedPage)
                else len(page.body)
            )
            try:
                self.coordinator.record_network_outcome(
                    lease,
                    permit,
                    ResponseClassification.SUCCESS,
                    transferred_bytes,
                )
            except Exception:
                return True
            try:
                result = self.parsers[lease.release_hash].parse(page)
                if (
                    result.capture_id != page.capture_id
                    or result.release_hash != lease.release_hash
                ):
                    raise ValueError("parser release or capture mismatch")
            except Exception:
                # Capture succeeded. Keep unknowns and advance partial normalization;
                # a parser miss must not request these bytes again.
                result = ParserResult(
                    page.capture_id,
                    lease.release_hash,
                    "unknown-variant",
                    (),
                    (
                        "title",
                        "price",
                        "currency",
                        "locality",
                        "area",
                        "rooms",
                        "description",
                        "availability",
                        "monthly_admin_fee",
                        "heating_type",
                        "admin_fee_includes_heating",
                    ),
                )
            artifact_id = None
            if result.missing_fields and self.artifact_writer is not None:
                try:
                    artifact_id = self.artifact_writer.store(self.source, page)
                except Exception:
                    # Diagnostics are optional; partial normalization must continue.
                    artifact_id = None
            outcome = CaptureOutcome(
                page.capture_id,
                page.fetched_at,
                hashlib.sha256(page.body).hexdigest(),
                len(page.body),
                result,
                artifact_id,
            )
            for attempt in range(3):
                if lost.is_set():
                    break
                try:
                    self.coordinator.complete(lease, outcome)
                    break
                except LostLease:
                    lost.set()
                except Exception:
                    # Retry the same metadata, never fetch again inside this lease.
                    # After bounded retries, expiry recovers uncertain worker loss.
                    if attempt < 2:
                        done.wait(0.1)
        finally:
            done.set()
            thread.join(timeout=self.heartbeat_seconds + 15)
        return True

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                try:
                    worked = self.run_once()
                except Exception:
                    worked = False
                if not worked:
                    self.stop.wait(self.idle_seconds)
        finally:
            with suppress(Exception):
                self._advertise(healthy=False)


def main() -> int:
    from homefinder.artifacts.client import HttpArtifactWriter
    from homefinder.parser_releases.package import load_packaged_parser
    from homefinder.scraper.coordinator import HttpCoordinatorClient
    from homefinder.scraper.http_connector import HttpResponseRequest
    from homefinder.scraper.transport import BoundedPageTransport

    parser = argparse.ArgumentParser(description="Source-pinned queued scrape worker")
    parser.add_argument(
        "--source", choices=("gratka", "morizon", "otodom", "olx"), required=True
    )
    parser.add_argument("--deployment", choices=("nas", "vps"), required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--coordinator-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--heartbeat-file", type=Path, required=True)
    parser.add_argument(
        "--dependency-lock-file",
        type=Path,
        default=Path("/app/release/requirements.lock"),
    )
    parser.add_argument("--proxy-pool-file", type=Path, required=True)
    parser.add_argument("--artifact-url", required=True)
    parser.add_argument("--artifact-token-file", type=Path, required=True)
    args = parser.parse_args()
    stop = Event()
    install_stop_signals(stop)
    coordinator = HttpCoordinatorClient(
        args.coordinator_url,
        args.token_file,
        identity=WorkerIdentity(
            args.worker_id, cast(Portal, args.source), args.deployment
        ),
    )
    source = cast(Portal, args.source)
    packaged = load_packaged_parser(
        source, dependency_lock_file=args.dependency_lock_file
    )
    ScrapeWorker(
        source=source,
        coordinator=coordinator,
        transport=BoundedPageTransport(
            request=HttpResponseRequest(args.proxy_pool_file)
        ),
        parsers={packaged.release_hash: packaged.parser},
        stop=stop,
        heartbeat_file=args.heartbeat_file,
        artifact_writer=HttpArtifactWriter(args.artifact_url, args.artifact_token_file),
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
