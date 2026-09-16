"""Network-free replay of retained NAS artifacts with the active parser."""

import argparse
import hashlib
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from typing import Protocol

from homefinder.parsers.contracts import PageInput, Parser, ParserResult, Portal
from homefinder.runtime import install_stop_signals, write_heartbeat
from homefinder.scrape_queue.contracts import (
    ArtifactReplayInput,
    ScrapeLease,
    TaskClass,
)


class ArtifactRecoveryCoordinator(Protocol):
    def register(self, releases: tuple[str, ...], healthy: bool = True) -> None: ...

    def claim_artifact_recovery(self) -> ScrapeLease | None: ...

    def heartbeat(self, lease: ScrapeLease) -> ScrapeLease: ...

    def replay_input(self, lease: ScrapeLease) -> ArtifactReplayInput: ...

    def complete_artifact_replay(
        self, lease: ScrapeLease, result: ParserResult
    ) -> None: ...

    def fail(self, lease: ScrapeLease, code: str) -> None: ...


class ArtifactRecoveryWorker:
    """Consume only artifact-recovery leases; raw bytes never leave this process."""

    def __init__(
        self,
        *,
        source: Portal,
        coordinator: ArtifactRecoveryCoordinator,
        read_artifact: Callable[[str, str], bytes],
        parsers: Mapping[str, Parser],
        stop: Event | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        heartbeat_seconds: float = 20,
        idle_seconds: float = 2,
        heartbeat_file: Path | None = None,
    ) -> None:
        if source not in {"gratka", "morizon", "otodom", "olx"}:
            raise ValueError("invalid worker source")
        self._source = source
        self._coordinator = coordinator
        self._read_artifact = read_artifact
        self._parsers = dict(parsers)
        self._stop = stop or Event()
        self._clock = clock
        self._heartbeat_seconds = heartbeat_seconds
        self._idle_seconds = idle_seconds
        self._heartbeat_file = heartbeat_file

    def _advertise(self, healthy: bool = True) -> None:
        self._coordinator.register(tuple(sorted(self._parsers)), healthy=healthy)
        if healthy and self._heartbeat_file is not None:
            write_heartbeat(self._heartbeat_file, self._clock())

    def run_once(self) -> bool:
        if self._stop.is_set():
            return False
        self._advertise()
        lease = self._coordinator.claim_artifact_recovery()
        if lease is None:
            return False
        if (
            lease.source != self._source
            or lease.task_class is not TaskClass.ARTIFACT_RECOVERY
            or lease.release_hash not in self._parsers
        ):
            self._coordinator.fail(lease, "invalid-recovery-lease")
            return True
        if (
            lease.lease_expires_at - self._clock()
        ).total_seconds() <= self._heartbeat_seconds:
            self._coordinator.fail(lease, "invalid-recovery-lease")
            return True
        done, lost = Event(), Event()

        def renew() -> None:
            while not done.wait(self._heartbeat_seconds):
                try:
                    self._coordinator.heartbeat(lease)
                    self._advertise()
                except Exception:
                    lost.set()
                    return

        try:
            self._coordinator.heartbeat(lease)
            thread = Thread(
                target=renew, name="artifact-recovery-heartbeat", daemon=True
            )
            thread.start()
            replay = self._coordinator.replay_input(lease)
            if not replay.artifact_token:
                raise ValueError("artifact capability missing")
            body = self._read_artifact(replay.artifact_id, replay.artifact_token)
            if (
                not isinstance(body, bytes)
                or not 0 < len(body) <= 2_000_000
                or hashlib.sha256(body).hexdigest() != replay.content_hash
            ):
                raise ValueError("invalid artifact")
            result = self._parsers[lease.release_hash].parse(
                PageInput(replay.capture_id, replay.fetched_at, body)
            )
            if (
                result.capture_id != replay.capture_id
                or result.release_hash != lease.release_hash
                or lost.is_set()
            ):
                raise ValueError("invalid parser result")
            self._coordinator.complete_artifact_replay(lease, result)
        except Exception:
            if not lost.is_set():
                with suppress(Exception):
                    self._coordinator.fail(lease, "artifact-recovery-failed")
        finally:
            done.set()
            if "thread" in locals():
                thread.join(timeout=self._heartbeat_seconds + 5)
        return True

    def run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    worked = self.run_once()
                except Exception:
                    worked = False
                if not worked:
                    self._stop.wait(self._idle_seconds)
        finally:
            with suppress(Exception):
                self._advertise(healthy=False)


def main() -> int:
    from typing import cast

    from homefinder.artifacts.client import HttpArtifactReader
    from homefinder.parser_releases.package import load_packaged_parser
    from homefinder.scrape_queue.contracts import WorkerIdentity
    from homefinder.scraper.coordinator import HttpCoordinatorClient

    parser = argparse.ArgumentParser(description="NAS artifact recovery worker")
    parser.add_argument(
        "--source", choices=("gratka", "morizon", "otodom", "olx"), required=True
    )
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--coordinator-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--artifact-url", required=True)
    parser.add_argument("--heartbeat-file", type=Path, required=True)
    parser.add_argument(
        "--dependency-lock-file",
        type=Path,
        default=Path("/app/release/requirements.lock"),
    )
    args = parser.parse_args()
    source = cast(Portal, args.source)
    packaged = load_packaged_parser(
        source, dependency_lock_file=args.dependency_lock_file
    )
    stop = Event()
    install_stop_signals(stop)
    coordinator = HttpCoordinatorClient(
        args.coordinator_url,
        args.token_file,
        identity=WorkerIdentity(args.worker_id, source, "nas"),
    )
    reader = HttpArtifactReader(args.artifact_url)
    ArtifactRecoveryWorker(
        source=source,
        coordinator=coordinator,
        read_artifact=reader.read,
        parsers={packaged.release_hash: packaged.parser},
        stop=stop,
        heartbeat_file=args.heartbeat_file,
    ).run()
    return 0


__all__ = ["ArtifactRecoveryCoordinator", "ArtifactRecoveryWorker"]


if __name__ == "__main__":
    raise SystemExit(main())
