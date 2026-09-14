import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def test_artifact_recovery_worker_replays_verified_bytes_without_network() -> None:
    from homefinder.artifact_recovery_worker import ArtifactRecoveryWorker
    from homefinder.parsers.contracts import PageFacts, ParserResult
    from homefinder.scrape_queue.contracts import (
        ArtifactReplayInput,
        ScrapeLease,
        TaskClass,
    )

    body = b"synthetic retained parser bytes"
    capture_id = uuid4()
    lease = ScrapeLease(
        uuid4(),
        "gratka",
        uuid4(),
        "https://gratka.pl/nieruchomosci/test/ob/10000001",
        TaskClass.ARTIFACT_RECOVERY,
        "a" * 64,
        2,
        uuid4(),
        NOW + timedelta(minutes=1),
        1,
    )
    replay = ArtifactReplayInput(
        capture_id,
        str(uuid4()),
        NOW - timedelta(days=1),
        hashlib.sha256(body).hexdigest(),
        NOW + timedelta(days=300),
    )
    events: list[object] = []

    class Coordinator:
        def claim_artifact_recovery(self):
            return lease

        def replay_input(self, claimed):
            assert claimed is lease
            return replay

        def complete_artifact_replay(self, claimed, result):
            events.append((claimed, result))

        def fail(self, claimed, code):
            events.append((claimed, code))

    class Parser:
        def parse(self, page):
            assert page.body == body
            assert page.fetched_at == replay.fetched_at
            return ParserResult(
                page.capture_id,
                "a" * 64,
                "synthetic-recovered",
                (),
                (),
                facts=PageFacts(title="Recovered synthetic page"),
            )

    worker = ArtifactRecoveryWorker(
        source="gratka",
        coordinator=Coordinator(),
        read_artifact=lambda artifact_id: body,
        parsers={"a" * 64: Parser()},
    )

    assert worker.run_once() is True
    assert len(events) == 1
    completed_lease, result = events[0]
    assert completed_lease is lease
    assert result.capture_id == capture_id
    assert result.facts.title == "Recovered synthetic page"
