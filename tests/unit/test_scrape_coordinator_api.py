import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
PREFIX = "/internal/scrape/v1"


@pytest.fixture
def coordinator(scrape_queue, tmp_path):
    from homefinder.scrape_queue.api import create_coordinator_router
    from homefinder.scrape_queue.budget import SourceBudgetRepository
    from homefinder.scrape_queue.contracts import SourceBudgetPolicy

    repo, snapshots, workers, sessions = scrape_queue
    budget = SourceBudgetRepository(
        sessions,
        policies={"gratka": SourceBudgetPolicy(timedelta(seconds=10), 1000, 1000)},
    )
    credentials = tmp_path / "synthetic-worker-identities.json"
    credentials.write_text(
        json.dumps(
            [
                {
                    "identity": asdict(worker),
                    "token_sha256": hashlib.sha256(
                        f"synthetic-{worker.deployment}".encode()
                    ).hexdigest(),
                }
                for worker in workers
            ]
        )
    )
    credentials.chmod(0o600)
    app = FastAPI()
    app.include_router(
        create_coordinator_router(
            repo, budget=budget, credentials_file=credentials, clock=lambda: NOW
        )
    )
    with TestClient(app) as client:
        yield client, repo, snapshots, credentials


def auth(deployment="nas"):
    return {"Authorization": f"Bearer synthetic-{deployment}"}


def test_coordinator_enforces_identity_and_rejects_raw_content(coordinator):
    client, repo, snapshots, _ = coordinator
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    assert client.post(PREFIX + "/claim", json={}).status_code == 401
    assert (
        client.post(
            PREFIX + "/claim", headers=auth(), json={"source": "morizon"}
        ).status_code
        == 422
    )
    response = client.post(PREFIX + "/claim", headers=auth(), json={})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    lease = response.json()
    assert lease["source"] == "gratka"
    assert "database" not in response.text
    assert (
        client.post(
            PREFIX + "/succeed", headers=auth("vps"), json={"lease": lease}
        ).status_code
        == 409
    )
    raw = "<script>synthetic forbidden body</script>"
    response = client.post(
        PREFIX + "/succeed", headers=auth(), json={"lease": lease, "raw_html": raw}
    )
    assert response.status_code == 422 and raw not in response.text
    assert (
        client.post(
            PREFIX + "/succeed", headers=auth(), json={"lease": lease}
        ).status_code
        == 200
    )
    assert (
        client.post(
            PREFIX + "/succeed", headers=auth(), json={"lease": lease}
        ).status_code
        == 409
    )


def test_coordinator_reserves_central_network_start(coordinator):
    client, repo, snapshots, _ = coordinator
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = client.post(PREFIX + "/claim", headers=auth(), json={}).json()

    response = client.post(
        PREFIX + "/network/reserve", headers=auth(), json={"lease": lease}
    )

    assert response.status_code == 200
    assert response.json()["granted"] is True
    assert response.json()["route_class"] == "direct"


def test_coordinator_bounds_payload_and_fails_closed_on_missing_auth(coordinator):
    client, _, _, credentials = coordinator
    response = client.post(PREFIX + "/claim", headers=auth(), content=b"x" * 16385)
    assert response.status_code == 413
    response = client.post(PREFIX + "/claim", headers=auth(), content=b"not-json")
    assert response.status_code == 422 and "not-json" not in response.text
    credentials.unlink()
    assert client.post(PREFIX + "/claim", headers=auth(), json={}).status_code == 503


def test_coordinator_heartbeat_status_and_task_class_scoping(coordinator):
    client, repo, snapshots, _ = coordinator
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    assert (
        client.post(
            PREFIX + "/workers/heartbeat",
            headers=auth(),
            json={
                "release_hashes": ["a" * 64],
                "healthy": True,
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            PREFIX + "/claim",
            headers=auth(),
            json={"task_classes": ["candidate_benchmark"]},
        ).status_code
        == 422
    )
    response = client.get(PREFIX + "/status", headers=auth())
    assert response.status_code == 200 and "gratka.pl" not in response.text
    assert client.post(PREFIX + "/enqueue", headers=auth(), json={}).status_code == 404
    assert client.post(PREFIX + "/activate", headers=auth(), json={}).status_code == 404


def test_web_app_keeps_coordinator_dark_by_default(tmp_path):
    from homefinder.config import Settings
    from homefinder.web.app import create_app

    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'web.sqlite'}"
    )
    with TestClient(create_app(settings)) as client:
        assert client.post(PREFIX + "/claim", json={}).status_code == 404
    with pytest.raises(ValueError, match="coordinator"):
        Settings(_env_file=None, concurrent_scraping_enabled=True)


def test_naive_deferral_and_unknown_cursor_fail_safely(coordinator):
    client, repo, snapshots, _ = coordinator
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = client.post(PREFIX + "/claim", headers=auth(), json={}).json()
    response = client.post(
        PREFIX + "/defer",
        headers=auth(),
        json={
            "lease": lease,
            "available_at": "2026-09-09T01:00:00",
            "code": "portal-denied",
        },
    )
    assert response.status_code == 422
    response = client.get(
        PREFIX + "/status", headers=auth(), params={"before": "broken"}
    )
    assert response.status_code == 422 and "broken" not in response.text


def test_enabled_web_app_mounts_private_coordinator(coordinator, scrape_queue):
    from homefinder.config import Settings
    from homefinder.web.app import create_app

    _, repo, snapshots, credentials = coordinator
    sessions = scrape_queue[3]
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    settings = Settings(
        _env_file=None,
        database_url=str(sessions.kw["bind"].url),
        concurrent_scraping_enabled=True,
        coordinator_credentials_file=credentials,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(PREFIX + "/status", headers=auth())
        assert response.status_code == 200
        assert len(response.json()["items"]) == 1
        assert response.headers["cache-control"] == "no-store"


def test_malformed_cursor_types_are_rejected(coordinator):
    import base64

    client, _, _, _ = coordinator
    cursor = base64.urlsafe_b64encode(
        json.dumps(["gratka", NOW.isoformat(), 123]).encode()
    ).decode()
    response = client.get(PREFIX + "/status", headers=auth(), params={"before": cursor})
    assert response.status_code == 422


def test_complete_endpoint_accepts_only_typed_production_handoff(coordinator):
    from uuid import uuid4

    client, repo, snapshots, _ = coordinator
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = client.post(PREFIX + "/claim", headers=auth(), json={}).json()
    capture_id = str(uuid4())
    payload = {
        "lease": lease,
        "outcome": {
            "capture_id": capture_id,
            "fetched_at": NOW.isoformat(),
            "content_hash": "a" * 64,
            "size_bytes": 10,
            "result": {
                "capture_id": capture_id,
                "release_hash": "a" * 64,
                "variant": "synthetic",
                "candidates": [],
                "missing_fields": ["rooms"],
                "facts": {"title": "Synthetic title"},
            },
        },
    }
    response = client.post(PREFIX + "/complete", headers=auth(), json=payload)
    assert response.status_code == 200
    assert (
        client.post(PREFIX + "/complete", headers=auth(), json=payload).status_code
        == 200
    )
    payload["outcome"]["result"]["raw_body"] = "synthetic forbidden raw"
    response = client.post(PREFIX + "/complete", headers=auth(), json=payload)
    assert (
        response.status_code == 422 and "synthetic forbidden raw" not in response.text
    )


def test_worker_startup_checks_configured_identity(coordinator):
    client, _, _, _ = coordinator
    response = client.post(
        PREFIX + "/workers/heartbeat",
        headers=auth(),
        json={
            "release_hashes": [],
            "healthy": True,
            "expected_identity": {
                "worker_id": "test-vps",
                "source": "gratka",
                "deployment": "vps",
            },
        },
    )
    assert response.status_code == 403


def test_http_worker_roundtrip_persists_capture_without_raw_body(coordinator, tmp_path):
    from threading import Event
    from uuid import uuid4

    from homefinder.parsers.contracts import PageFacts, PageInput, ParserResult
    from homefinder.scraper.coordinator import HttpCoordinatorClient
    from homefinder.scraper.worker import ScrapeWorker

    client, repo, snapshots, _ = coordinator
    repo.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    token = tmp_path / "worker-token"
    token.write_text("synthetic-nas")
    token.chmod(0o600)
    requests = []

    def request(path, payload, bearer):
        requests.append(payload)
        response = client.post(
            path, headers={"Authorization": "Bearer " + bearer}, json=payload
        )
        assert response.status_code == 200
        return response.content

    class Transport:
        def fetch(self, request):
            return PageInput(uuid4(), NOW, b"never-persist-this-synthetic-raw-body")

    class Parser:
        def parse(self, page):
            return ParserResult(
                page.capture_id,
                "a" * 64,
                "synthetic",
                (),
                ("rooms",),
                facts=PageFacts(title="Synthetic roundtrip"),
            )

    worker = ScrapeWorker(
        source="gratka",
        coordinator=HttpCoordinatorClient("http://web:8000", token, request=request),
        transport=Transport(),
        parsers={"a" * 64: Parser()},
        stop=Event(),
        clock=lambda: NOW,
    )
    assert worker.run_once()
    assert repo.outcome(source="gratka", snapshot_id=snapshots[0]).facts.title == (
        "Synthetic roundtrip"
    )
    assert "never-persist-this-synthetic-raw-body" not in json.dumps(requests)


def test_coordinator_failure_does_not_log_result_or_database_details(
    coordinator, monkeypatch
):
    from homefinder.scrape_queue.repository import ScrapeQueueRepository

    client, _, _, _ = coordinator

    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic-private-database-detail")

    monkeypatch.setattr(ScrapeQueueRepository, "status", unavailable)
    response = client.get(PREFIX + "/status", headers=auth())
    assert response.status_code == 503
    assert "synthetic-private-database-detail" not in response.text
