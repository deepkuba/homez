"""Network-free replay of retained NAS artifacts with the active parser."""

import hashlib
from collections.abc import Callable, Mapping
from typing import Protocol

from homefinder.parsers.contracts import PageInput, Parser, ParserResult, Portal
from homefinder.scrape_queue.contracts import (
    ArtifactReplayInput,
    ScrapeLease,
    TaskClass,
)


class ArtifactRecoveryCoordinator(Protocol):
    def claim_artifact_recovery(self) -> ScrapeLease | None: ...

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
    ) -> None:
        if source not in {"gratka", "morizon", "otodom", "olx"}:
            raise ValueError("invalid worker source")
        self._source = source
        self._coordinator = coordinator
        self._read_artifact = read_artifact
        self._parsers = dict(parsers)

    def run_once(self) -> bool:
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
        try:
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
            ):
                raise ValueError("invalid parser result")
            self._coordinator.complete_artifact_replay(lease, result)
        except Exception:
            self._coordinator.fail(lease, "artifact-recovery-failed")
        return True


__all__ = ["ArtifactRecoveryCoordinator", "ArtifactRecoveryWorker"]
