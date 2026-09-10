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
