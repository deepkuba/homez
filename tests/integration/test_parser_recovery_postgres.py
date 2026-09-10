import os
from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_activation_replays_only_newest_capture_without_fetch(scrape_queue) -> None:
    from homefinder.parser_recovery import ParserRecoveryRepository

    queue, snapshots, _workers, sessions = scrape_queue
    recovery = ParserRecoveryRepository(sessions)

    planned = recovery.plan_artifact_recovery(
        source="gratka",
        release_hash="a" * 64,
        activation_epoch=1,
        now=NOW,
    )

    assert planned == ()
    assert queue.status(source="gratka").items == ()
    assert len(snapshots) == 3  # planning never invents or fetches a capture
