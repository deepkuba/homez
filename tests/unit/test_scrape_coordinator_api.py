import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
PREFIX = "/internal/scrape/v1"


@pytest.fixture
def coordinator(scrape_queue, tmp_path):
    from homefinder.scrape_queue.api import create_coordinator_router

    repo, snapshots, workers, _ = scrape_queue
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
        create_coordinator_router(repo, credentials_file=credentials, clock=lambda: NOW)
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
