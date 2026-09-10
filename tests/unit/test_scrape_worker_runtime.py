from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import uuid4

import pytest

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def lease():
    from homefinder.scrape_queue.contracts import ScrapeLease, TaskClass

    return ScrapeLease(
        uuid4(),
        "gratka",
        uuid4(),
        "https://gratka.pl/nieruchomosci/synthetic/ob/10000001",
        TaskClass.LIVE,
        "a" * 64,
        1,
        uuid4(),
        NOW + timedelta(seconds=60),
        1,
    )


class Coordinator:
    def __init__(self):
        self.leased = lease()
        self.claims = 0
        self.renewed = Event()
        self.completed = []
        self.failures = []
        self.registrations = []
        self.reservations = []

    def register(self, releases, healthy=True):
        self.registrations.append((releases, healthy))

    def claim(self):
        self.claims += 1
        return self.leased if self.claims == 1 else None

    def reserve_start(self, job):
        from homefinder.scrape_queue.contracts import NetworkPermit

        self.reservations.append(job)
        return NetworkPermit(True, NOW, "direct")

    def heartbeat(self, job):
        self.renewed.set()
        return job

    def complete(self, job, outcome):
        self.completed.append(outcome)

    def fail(self, job, code):
        self.failures.append(code)


def test_worker_requires_central_network_permit_before_fetch():
    from homefinder.parsers.contracts import PageFacts, PageInput, ParserResult
    from homefinder.scrape_queue.contracts import NetworkPermit
    from homefinder.scraper.worker import ScrapeWorker

    coordinator = Coordinator()
    coordinator.reserve_start = lambda job: NetworkPermit(
        True, NOW, "proxy", "opaque-route-a"
    )
    requests = []

    class Transport:
        def fetch(self, request):
            requests.append(request)
            return PageInput(uuid4(), NOW, b"synthetic")

    class Parser:
        def parse(self, page):
            return ParserResult(
                page.capture_id,
                "a" * 64,
                "synthetic",
                (),
                (),
                facts=PageFacts(title="Synthetic"),
            )

    ScrapeWorker(
        source="gratka",
        coordinator=coordinator,
        transport=Transport(),
        parsers={"a" * 64: Parser()},
        stop=Event(),
        clock=lambda: NOW,
    ).run_once()

    assert requests[0].route_id == "opaque-route-a"


def test_worker_heartbeats_during_fetch_and_drains_on_shutdown():
    from homefinder.parsers.contracts import PageFacts, PageInput, ParserResult
    from homefinder.scraper.worker import ScrapeWorker

    coordinator, stop = Coordinator(), Event()

    class Transport:
        def fetch(self, request):
            assert coordinator.renewed.wait(timeout=2)
            stop.set()
            return PageInput(uuid4(), NOW, b"synthetic")

    class Parser:
        def parse(self, page):
            return ParserResult(
                page.capture_id,
                "a" * 64,
                "synthetic",
                (),
                (),
                facts=PageFacts(title="Synthetic"),
            )

    worker = ScrapeWorker(
        source="gratka",
        coordinator=coordinator,
        transport=Transport(),
        parsers={"a" * 64: Parser()},
        stop=stop,
        clock=lambda: NOW,
        heartbeat_seconds=0.01,
    )
    worker.run()
    assert coordinator.claims == 1
    assert len(coordinator.completed) == 1
    assert coordinator.registrations[-1] == (("a" * 64,), False)


def test_worker_never_fetches_unknown_release_or_foreign_source():
    from dataclasses import replace

    from homefinder.scraper.worker import ScrapeWorker

    class Transport:
        def fetch(self, request):
            pytest.fail("must not fetch unsupported work")

    for override in ({"release_hash": "b" * 64}, {"source": "morizon"}):
        coordinator = Coordinator()
        coordinator.leased = replace(coordinator.leased, **override)
        worker = ScrapeWorker(
            source="gratka",
            coordinator=coordinator,
            transport=Transport(),
            parsers={"a" * 64: object()},
            stop=Event(),
            clock=lambda: NOW,
        )
        assert worker.run_once() is True
        assert coordinator.completed == []


def test_lost_heartbeat_discards_result_without_refetch():
    from homefinder.parsers.contracts import PageInput
    from homefinder.scrape_queue.contracts import LostLease
    from homefinder.scraper.worker import ScrapeWorker

    coordinator = Coordinator()
    rejected = Event()

    def heartbeat(job):
        rejected.set()
        raise LostLease("synthetic lease lost")

    coordinator.heartbeat = heartbeat
    calls = []

    class Transport:
        def fetch(self, request):
            calls.append(request)
            assert rejected.wait(timeout=2)
            return PageInput(uuid4(), NOW, b"synthetic")

    class Parser:
        def parse(self, page):
            pytest.fail("withdrawn lease must not parse")

    worker = ScrapeWorker(
        source="gratka",
        coordinator=coordinator,
        transport=Transport(),
        parsers={"a" * 64: Parser()},
        stop=Event(),
        clock=lambda: NOW,
        heartbeat_seconds=0.01,
    )
    worker.run_once()
    assert len(calls) == 1 and not coordinator.completed


def test_default_transport_has_no_network_permission():
    from homefinder.scraper.contracts import FetchRequest
    from homefinder.scraper.transport import DisabledPageTransport, NetworkNotReleased

    with pytest.raises(NetworkNotReleased):
        DisabledPageTransport().fetch(FetchRequest("gratka", "https://example.invalid"))


def test_legacy_parser_can_parse_bounded_bytes_without_fetch():
    from homefinder.sources.portal_pages import PortalPageScraper

    def no_fetch(*args):
        pytest.fail("parse_bytes must never fetch")

    parser = PortalPageScraper("gratka", fetcher=no_fetch)
    with pytest.raises(ValueError):
        parser.parse_bytes(
            "https://gratka.pl/nieruchomosci/test/ob/10000001", b"x" * 2_000_001
        )
    with pytest.raises(ValueError):
        parser.parse_bytes(
            "https://gratka.pl/nieruchomosci/test/ob/10000001",
            b"synthetic missing fields",
        )


def test_captured_parser_failure_completes_unknowns_without_retry():
    from homefinder.parsers.contracts import PageInput
    from homefinder.scraper.worker import ScrapeWorker

    coordinator = Coordinator()
    calls = []

    class Transport:
        def fetch(self, request):
            calls.append(request)
            return PageInput(uuid4(), NOW, b"synthetic")

    class Parser:
        def parse(self, page):
            raise ValueError("synthetic parser failure")

    worker = ScrapeWorker(
        source="gratka",
        coordinator=coordinator,
        transport=Transport(),
        parsers={"a" * 64: Parser()},
        stop=Event(),
        clock=lambda: NOW,
    )
    worker.run_once()
    assert len(calls) == 1 and len(coordinator.completed) == 1
    assert coordinator.completed[0].result.missing_fields


def test_artifact_outage_completes_partial_without_refetch_or_local_fallback(
    tmp_path, monkeypatch
):
    from homefinder.parsers.contracts import PageFacts, PageInput, ParserResult
    from homefinder.scraper.worker import ScrapeWorker

    coordinator = Coordinator()
    calls = []

    class Transport:
        def fetch(self, request):
            calls.append(request)
            return PageInput(uuid4(), NOW, b"synthetic diagnostic")

    class Parser:
        def parse(self, page):
            return ParserResult(
                page.capture_id,
                "a" * 64,
                "synthetic",
                (),
                ("rooms",),
                facts=PageFacts(title="Synthetic partial"),
            )

    class UnavailableArtifacts:
        def store(self, source, page):
            raise OSError("synthetic NAS outage")

    monkeypatch.chdir(tmp_path)
    worker = ScrapeWorker(
        source="gratka",
        coordinator=coordinator,
        transport=Transport(),
        parsers={"a" * 64: Parser()},
        artifact_writer=UnavailableArtifacts(),
        stop=Event(),
        clock=lambda: NOW,
    )
    assert worker.run_once()
    assert len(calls) == 1
    assert coordinator.completed[0].artifact_id is None
    assert coordinator.completed[0].result.facts.title == "Synthetic partial"
    assert list(tmp_path.iterdir()) == []
    assert coordinator.completed[0].result.facts.price_minor is None


def test_ambiguous_ack_retries_same_result_without_refetch():
    from homefinder.parsers.contracts import PageFacts, PageInput, ParserResult
    from homefinder.scraper.worker import ScrapeWorker

    coordinator = Coordinator()
    acknowledgements, fetches = [], []

    def complete(job, outcome):
        acknowledgements.append(outcome)
        if len(acknowledgements) == 1:
            raise OSError("synthetic lost acknowledgement")
        coordinator.completed.append(outcome)

    coordinator.complete = complete

    class Transport:
        def fetch(self, request):
            fetches.append(request)
            return PageInput(uuid4(), NOW, b"synthetic")

    class Parser:
        def parse(self, page):
            return ParserResult(
                page.capture_id,
                "a" * 64,
                "synthetic",
                (),
                (),
                facts=PageFacts(title="Synthetic"),
            )

    worker = ScrapeWorker(
        source="gratka",
        coordinator=coordinator,
        transport=Transport(),
        parsers={"a" * 64: Parser()},
        stop=Event(),
        clock=lambda: NOW,
    )
    worker.run_once()
    assert len(fetches) == 1 and len(acknowledgements) == 2
    assert acknowledgements[0] is acknowledgements[1]
    assert not coordinator.failures


def test_transport_failure_is_not_misclassified_as_parser_failure():
    from homefinder.scraper.worker import ScrapeWorker

    coordinator = Coordinator()

    class Transport:
        def fetch(self, request):
            raise OSError("synthetic failed transport")

    ScrapeWorker(
        source="gratka",
        coordinator=coordinator,
        transport=Transport(),
        parsers={"a" * 64: object()},
        stop=Event(),
        clock=lambda: NOW,
    ).run_once()
    assert coordinator.failures == ["transport-error"]


def test_worker_rejects_lease_shorter_than_heartbeat_interval():
    from dataclasses import replace

    from homefinder.scraper.worker import ScrapeWorker

    coordinator = Coordinator()
    coordinator.leased = replace(
        coordinator.leased, lease_expires_at=NOW + timedelta(seconds=2)
    )

    class Transport:
        def fetch(self, request):
            pytest.fail("lease cannot support configured heartbeat")

    ScrapeWorker(
        source="gratka",
        coordinator=coordinator,
        transport=Transport(),
        parsers={"a" * 64: object()},
        stop=Event(),
        clock=lambda: NOW,
    ).run_once()
    assert not coordinator.completed
