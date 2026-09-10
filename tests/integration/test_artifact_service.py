"""Synthetic, network-free contracts for NAS-only parser diagnostics."""

import builtins
import io
from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import uuid4

from pydantic import TypeAdapter

from homefinder.parsers.contracts import PageFacts, PageInput, ParserResult
from homefinder.scrape_queue.contracts import CaptureOutcome, ScrapeLease, TaskClass
from homefinder.scraper.worker import ScrapeWorker


def test_vps_streams_failure_bytes_without_local_persistence(
    tmp_path, monkeypatch, caplog
):
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    # Hand-authored bytes deliberately cannot survive a text-decoding round trip.
    raw = b"synthetic parser diagnostic\x00\xff\x80\r\n"
    page = PageInput(uuid4(), now, raw)
    release = "a" * 64
    artifact_id = str(uuid4())
    leased = ScrapeLease(
        uuid4(),
        "gratka",
        uuid4(),
        "https://example.invalid/listing/synthetic",
        TaskClass.LIVE,
        release,
        1,
        uuid4(),
        now + timedelta(seconds=60),
        1,
    )
    fetched, uploads, completed = [], [], []
    nas_received = bytearray()

    class Transport:
        def fetch(self, request):
            fetched.append(request)
            return page

    class Parser:
        def parse(self, supplied):
            assert supplied.body == raw
            return ParserResult(
                supplied.capture_id,
                release,
                "synthetic",
                (),
                ("area", "rooms"),
                facts=PageFacts(title="Synthetic partial listing", price_minor=100),
            )

    class NasUpload:
        def store(self, source, supplied):
            uploads.append((source, supplied.capture_id))
            # The transport boundary consumes the bounded bytes in memory only.
            stream = io.BytesIO(supplied.body)
            while chunk := stream.read(7):
                nas_received.extend(chunk)
            return artifact_id

    class Coordinator:
        def register(self, releases, healthy=True):
            assert releases == (release,)

        def claim(self):
            return leased

        def reserve_start(self, lease):
            from homefinder.scrape_queue.contracts import NetworkPermit

            return NetworkPermit(True, now, "direct")

        def record_network_outcome(self, *args, **kwargs):
            return None

        def heartbeat(self, lease):
            return lease

        def complete(self, lease, outcome):
            completed.append(outcome)

        def fail(self, lease, code):
            raise AssertionError(f"partial extraction must complete: {code}")

    worker = ScrapeWorker(
        source="gratka",
        coordinator=Coordinator(),
        transport=Transport(),
        parsers={release: Parser()},
        artifact_writer=NasUpload(),
        stop=Event(),
        clock=lambda: now,
    )
    monkeypatch.chdir(tmp_path)

    def reject_file_access(*args, **kwargs):
        raise AssertionError("VPS diagnostic processing must remain in memory")

    with monkeypatch.context() as guarded:
        guarded.setattr(builtins, "open", reject_file_access)
        guarded.setattr(io, "open", reject_file_access)
        assert worker.run_once() is True

    assert len(fetched) == 1
    assert uploads == [("gratka", page.capture_id)]
    assert bytes(nas_received) == raw
    assert len(completed) == 1
    outcome = completed[0]
    assert outcome.artifact_id == artifact_id
    assert outcome.result.missing_fields == ("area", "rooms")
    assert outcome.result.facts.price_minor == 100
    assert outcome.result.facts.title == "Synthetic partial listing"
    assert b"synthetic parser diagnostic" not in TypeAdapter(CaptureOutcome).dump_json(
        outcome
    )
    assert "synthetic parser diagnostic" not in caplog.text
    assert list(tmp_path.iterdir()) == []
