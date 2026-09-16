import json
from pathlib import Path

import pytest


def test_client_uses_bounded_private_control_requests(tmp_path):
    from homefinder.scraper.coordinator import HttpCoordinatorClient

    token = tmp_path / "synthetic-token"
    token.write_text("synthetic-worker-token")
    token.chmod(0o600)
    calls = []

    def request(path, payload, bearer):
        calls.append((path, payload, bearer))
        return b"null"

    client = HttpCoordinatorClient("http://web:8000", token, request=request)
    client.register(("a" * 64,))
    assert client.claim() is None
    assert calls[0][0] == "/internal/scrape/v1/workers/heartbeat"
    assert calls[0][2] == "synthetic-worker-token"
    assert calls[1][1] == {"task_classes": ["live", "network_recovery"]}


def test_recovery_client_uses_artifact_only_control_paths(tmp_path):
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from homefinder.parsers.contracts import PageFacts, ParserResult
    from homefinder.scrape_queue.contracts import ScrapeLease, TaskClass
    from homefinder.scraper.coordinator import HttpCoordinatorClient

    token = tmp_path / "synthetic-token"
    token.write_text("synthetic-worker-token")
    token.chmod(0o600)
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    lease = ScrapeLease(
        uuid4(),
        "gratka",
        uuid4(),
        "https://gratka.pl/nieruchomosci/test/ob/10000001",
        TaskClass.ARTIFACT_RECOVERY,
        "a" * 64,
        2,
        uuid4(),
        now + timedelta(minutes=1),
        1,
    )
    calls = []
    responses = iter(
        (
            json.dumps(
                {
                    **lease.__dict__,
                    "lease_token": str(lease.lease_token),
                    "task_id": str(lease.task_id),
                    "snapshot_id": str(lease.snapshot_id),
                    "task_class": lease.task_class.value,
                    "lease_expires_at": lease.lease_expires_at.isoformat(),
                }
            ).encode(),
            json.dumps(
                {
                    "capture_id": str(uuid4()),
                    "artifact_id": str(uuid4()),
                    "fetched_at": now.isoformat(),
                    "content_hash": "b" * 64,
                    "result_expires_at": (now + timedelta(days=1)).isoformat(),
                }
            ).encode(),
            b"null",
        )
    )

    def request(path, payload, bearer):
        calls.append((path, payload, bearer))
        return next(responses)

    client = HttpCoordinatorClient("http://web:8000", token, request=request)
    claimed = client.claim_artifact_recovery()
    replay = client.replay_input(claimed)
    client.complete_artifact_replay(
        claimed,
        ParserResult(
            replay.capture_id,
            "a" * 64,
            "synthetic",
            (),
            (),
            facts=PageFacts(title="Recovered"),
        ),
    )

    assert [call[0] for call in calls] == [
        "/internal/scrape/v1/artifact/claim",
        "/internal/scrape/v1/artifact/input",
        "/internal/scrape/v1/artifact/complete",
    ]
    assert all("raw" not in json.dumps(call[1]) for call in calls)


def test_client_requests_central_network_permit(tmp_path):
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from homefinder.scrape_queue.contracts import ScrapeLease, TaskClass
    from homefinder.scraper.coordinator import HttpCoordinatorClient

    token = tmp_path / "synthetic-token"
    token.write_text("synthetic-worker-token")
    token.chmod(0o600)
    calls = []

    def request(path, payload, bearer):
        calls.append((path, payload, bearer))
        return json.dumps(
            {
                "granted": True,
                "available_at": "2026-09-10T00:00:00Z",
                "route_class": "proxy",
                "route_id": "opaque-route-a",
            }
        ).encode()

    lease = ScrapeLease(
        uuid4(),
        "gratka",
        uuid4(),
        "https://gratka.pl/nieruchomosci/synthetic/ob/10000001",
        TaskClass.LIVE,
        "a" * 64,
        1,
        uuid4(),
        datetime(2026, 9, 10, tzinfo=timezone.utc) + timedelta(seconds=60),
        1,
    )
    permit = HttpCoordinatorClient(
        "http://web:8000", token, request=request
    ).reserve_start(lease)

    assert permit.route_id == "opaque-route-a"
    assert calls[0][0] == "/internal/scrape/v1/network/reserve"
    assert "canonical_url" in calls[0][1]["lease"]
    assert calls[0][1]["requested_proxy_bytes"] == 2_000_000


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com",
        "http://169.254.169.254",
        "http://127.0.0.1",
        "http://web:8000/path",
        "http://user:secret@web:8000",
    ],
)
def test_client_rejects_non_private_coordinator_endpoints(endpoint):
    from homefinder.scraper.coordinator import HttpCoordinatorClient

    with pytest.raises(ValueError):
        HttpCoordinatorClient(endpoint, Path("/unused"))


def test_client_does_not_echo_unsafe_response(tmp_path):
    from homefinder.scraper.coordinator import (
        CoordinatorUnavailable,
        HttpCoordinatorClient,
    )

    token = tmp_path / "synthetic-token"
    token.write_text("synthetic-worker-token")
    token.chmod(0o600)
    client = HttpCoordinatorClient(
        "http://web:8000",
        token,
        request=lambda *args: json.dumps("raw content").encode(),
    )
    with pytest.raises(CoordinatorUnavailable) as error:
        client.claim()
    assert "raw content" not in str(error.value)


def test_untrusted_response_is_absent_from_exception_traceback(tmp_path):
    import traceback

    from homefinder.scraper.coordinator import (
        CoordinatorUnavailable,
        HttpCoordinatorClient,
    )

    token = tmp_path / "synthetic-token"
    token.write_text("synthetic-worker-token")
    token.chmod(0o600)
    client = HttpCoordinatorClient(
        "http://100.64.0.1:18000",
        token,
        request=lambda *args: b'"sensitive-response-marker"',
    )
    with pytest.raises(CoordinatorUnavailable) as caught:
        client.claim()
    rendered = "".join(traceback.format_exception(caught.value))
    assert "input_value=" not in rendered
