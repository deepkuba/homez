import json
from pathlib import Path

import pytest

from homefinder.scraper.contracts import FetchRequest

URL = "https://gratka.pl/nieruchomosci/synthetic/ob/10000001"


def test_connector_resolves_only_granted_proxy_route_from_secret(
    tmp_path: Path,
) -> None:
    from homefinder.scraper.http_connector import HttpResponseRequest

    secret = tmp_path / "proxy-pool.json"
    secret.write_text(
        json.dumps(
            [
                {
                    "route_id": "route-a",
                    "proxy_url": (
                        "http://synthetic-user:synthetic-password@proxy.invalid:8080"
                    ),
                }
            ]
        )
    )
    secret.chmod(0o600)
    connections = []

    class Connection:
        def __init__(self, host, port, timeout):
            self.host, self.port, self.timeout = host, port, timeout
            self.tunnel = None
            self.sent = None
            connections.append(self)

        def set_tunnel(self, host, port, headers):
            self.tunnel = (host, port, headers)

        def request(self, method, target, headers):
            self.sent = (method, target, headers)

        def getresponse(self):
            return object()

        def close(self):
            pass

    connector = HttpResponseRequest(secret, connection_factory=Connection)
    connector(FetchRequest("gratka", URL, "route-a"), timeout_seconds=10)

    connection = connections[0]
    assert (connection.host, connection.port) == ("proxy.invalid", 8080)
    assert connection.tunnel[:2] == ("gratka.pl", 443)
    assert connection.sent[:2] == (
        "GET",
        "/nieruchomosci/synthetic/ob/10000001",
    )
    assert "synthetic-password" not in repr(connector)


def test_connector_rejects_unknown_route_without_connection(tmp_path: Path) -> None:
    from homefinder.scraper.contracts import BoundedTransportError
    from homefinder.scraper.http_connector import HttpResponseRequest

    secret = tmp_path / "proxy-pool.json"
    secret.write_text("[]")
    secret.chmod(0o600)

    with pytest.raises(BoundedTransportError, match="route unavailable"):
        HttpResponseRequest(
            secret,
            connection_factory=lambda *args: pytest.fail("must not connect"),
        )(FetchRequest("gratka", URL, "route-a"), timeout_seconds=10)


def test_proxy_authentication_failure_is_typed_without_secret(tmp_path: Path) -> None:
    from homefinder.scraper.contracts import BoundedTransportError
    from homefinder.scraper.denial_policy import FailureEvidence
    from homefinder.scraper.http_connector import HttpResponseRequest

    password = "synthetic-password"
    secret = tmp_path / "proxy-pool.json"
    secret.write_text(
        json.dumps(
            [
                {
                    "route_id": "route-a",
                    "proxy_url": f"http://user:{password}@proxy.invalid:8080",
                }
            ]
        )
    )
    secret.chmod(0o600)

    class Connection:
        def set_tunnel(self, host, port, headers):
            pass

        def request(self, method, target, headers):
            raise OSError("Tunnel connection failed: 407 Proxy Authentication Required")

        def close(self):
            pass

    with pytest.raises(BoundedTransportError) as caught:
        HttpResponseRequest(secret, connection_factory=lambda *args: Connection())(
            FetchRequest("gratka", URL, "route-a"), timeout_seconds=10
        )

    assert caught.value.evidence.failure is FailureEvidence.PROXY_AUTHENTICATION
    assert password not in str(caught.value)
