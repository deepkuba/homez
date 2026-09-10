import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _build(marker: str):
    from homefinder.parser_releases import ReleaseBuild

    return ReleaseBuild(
        source="gratka",
        parser_version=f"gratka-v{marker}",
        git_commit=marker * 40,
        parser_content_hash=marker * 64,
        configuration_hash="3" * 64,
        dependency_lock_hash="4" * 64,
        deployable_digest="sha256:" + marker * 64,
        qualifying_benchmark_run=f"benchmark-gratka-v{marker}",
    )


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_activation_rejects_missing_worker_capability(scrape_queue) -> None:
    from homefinder.parser_releases import (
        ActivationRejected,
        ParserReleaseRepository,
    )

    _queue, _snapshots, _workers, sessions = scrape_queue
    releases = ParserReleaseRepository(sessions, eligibility=lambda *args: True)
    candidate = releases.register(
        _build("2"),
        now=NOW,
    )

    with pytest.raises(
        ActivationRejected,
        match="healthy NAS and VPS workers must advertise candidate and rollback",
    ):
        releases.activate(
            source="gratka",
            release_hash=candidate.release_hash,
            actor="operator@example.test",
            compared_metrics="candidate reviewed against active release",
            now=NOW,
        )


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_concurrent_activation_commits_one_portal_pointer(scrape_queue) -> None:
    from homefinder.catalog.orm import ParserActivationAuditRecord
    from homefinder.parser_releases import ActivationRejected, ParserReleaseRepository

    queue, _snapshots, workers, sessions = scrape_queue
    releases = ParserReleaseRepository(sessions, eligibility=lambda *args: True)
    candidates = [releases.register(_build(marker), now=NOW) for marker in ("6", "7")]
    supported = ("a" * 64, *(candidate.release_hash for candidate in candidates))
    for worker in workers:
        queue.register_worker(worker, release_hashes=supported, now=NOW)
    barrier = Barrier(2)

    def activate(release_hash: str) -> bool:
        barrier.wait(timeout=10)
        try:
            releases.activate(
                source="gratka",
                release_hash=release_hash,
                actor="operator@example.test",
                compared_metrics="reviewed concurrent candidate",
                expected_epoch=1,
                now=NOW,
            )
            return True
        except ActivationRejected:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(activate, [item.release_hash for item in candidates])) == 1
    with sessions() as session:
        assert session.query(ParserActivationAuditRecord).count() == 1
