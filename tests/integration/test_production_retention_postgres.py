import os
from datetime import datetime, timezone

import pytest


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_replay_does_not_extend_capture_retention(scrape_queue) -> None:
    from homefinder.operations.retention import ProductionRetentionRepository

    _queue, _snapshots, _workers, sessions = scrape_queue
    result = ProductionRetentionRepository(sessions).run_daily(
        now=datetime(2029, 9, 10, tzinfo=timezone.utc), batch_size=50
    )

    assert result.deleted_results == 0
    assert result.oldest_remaining_fetched_at is None
