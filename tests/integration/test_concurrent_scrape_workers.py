import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Event

import pytest

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_both_deployments_complete_shared_queue_work(scrape_queue):
    from homefinder.parsers.contracts import PageFacts, PageInput, ParserResult
    from homefinder.scraper.worker import ScrapeWorker

    repo, snapshots, identities, sessions = scrape_queue
    barrier = Barrier(2)
    fetched = []

    class Transport:
        def fetch(self, request):
            from uuid import uuid4

            fetched.append(request.canonical_url)
            barrier.wait(timeout=5)
            return PageInput(uuid4(), NOW, b"synthetic parser bytes")

    class Parser:
        def parse(self, page):
            return ParserResult(
                page.capture_id,
                "a" * 64,
                "synthetic",
                (),
                (),
                facts=PageFacts(
                    title="Synthetic page", price_minor=200, currency="PLN"
                ),
            )

    class Coordinator:
        def __init__(self, identity):
            self.identity = identity

        def register(self, releases, healthy=True):
            repo.register_worker(
                self.identity, release_hashes=releases, healthy=healthy, now=NOW
            )

        def claim(self):
            return repo.claim(self.identity, now=NOW)

        def heartbeat(self, lease):
            return repo.heartbeat(self.identity, lease, now=NOW)

        def complete(self, lease, outcome):
            repo.complete(self.identity, lease, outcome, now=NOW)

        def fail(self, lease, code):
            repo.fail(self.identity, lease, code=code, now=NOW)

    for snapshot in snapshots[:2]:
        repo.enqueue(source="gratka", snapshot_id=snapshot, now=NOW)
    workers = [
        ScrapeWorker(
            source="gratka",
            coordinator=Coordinator(identity),
            transport=Transport(),
            parsers={"a" * 64: Parser()},
            stop=Event(),
            clock=lambda: NOW,
        )
        for identity in identities
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda worker: worker.run_once(), workers)) == [True, True]
    assert len(fetched) == len(set(fetched)) == 2
    assert {row.state for row in repo.status(source="gratka").items} == {"succeeded"}
    for snapshot in snapshots[:2]:
        outcome = repo.outcome(source="gratka", snapshot_id=snapshot)
        assert outcome.facts.title == "Synthetic page"
