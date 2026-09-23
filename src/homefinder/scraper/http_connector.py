"""Bounded direct/proxy connector with secret-file-only route resolution."""

import base64
import http.client
import re
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import SplitResult, unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter

from homefinder.scraper.contracts import BoundedTransportError, FetchRequest
from homefinder.scraper.denial_policy import FailureEvidence
from homefinder.sources.gmail import read_secret_text
from homefinder.sources.portal_pages import validate_listing_url

_ROUTE = re.compile(r"[a-zA-Z0-9_-]{1,64}")


class _ProxyRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    route_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    proxy_url: SecretStr


class _Connection(Protocol):
    def set_tunnel(self, host: str, port: int, headers: dict[str, str]) -> None: ...

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None: ...

    def getresponse(self) -> http.client.HTTPResponse: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str, int, float], _Connection]


def _https_connection(host: str, port: int, timeout: float) -> _Connection:
    return http.client.HTTPSConnection(host, port, timeout=timeout)


class _OwnedResponse:
    def __init__(self, response: http.client.HTTPResponse, connection: _Connection):
        self._response = response
        self._connection = connection

    @property
    def status(self) -> int:
        return self._response.status

    def getheader(self, name: str) -> str | None:
        return self._response.getheader(name)

    def read(self, size: int) -> bytes:
        return self._response.read(size)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


class HttpResponseRequest:
    """Resolve opaque routes locally and return a no-redirect response stream."""

    def __init__(
        self,
        proxy_pool_file: Path,
        *,
        connection_factory: ConnectionFactory = _https_connection,
    ) -> None:
        self._proxy_pool_file = proxy_pool_file
        self._connection_factory = connection_factory

    def __call__(
        self, request: FetchRequest, *, timeout_seconds: float
    ) -> _OwnedResponse:
        canonical, _ = validate_listing_url(request.source, request.canonical_url)
        target = urlsplit(canonical)
        connection: _Connection | None = None
        try:
            if request.route_id is None:
                connection = self._connection_factory(
                    target.hostname or "", target.port or 443, timeout_seconds
                )
            else:
                proxy = self._resolve_proxy(request.route_id)
                connection = self._connection_factory(
                    proxy.hostname or "", proxy.port or 80, timeout_seconds
                )
                credentials = (
                    f"{unquote(proxy.username or '')}:{unquote(proxy.password or '')}"
                )
                authorization = base64.b64encode(credentials.encode()).decode("ascii")
                connection.set_tunnel(
                    target.hostname or "",
                    target.port or 443,
                    {"Proxy-Authorization": f"Basic {authorization}"},
                )
            path = target.path or "/"
            if target.query:
                path += "?" + target.query
            connection.request(
                "GET",
                path,
                headers={
                    "Accept-Encoding": "gzip, deflate",
                    "User-Agent": "homefinder-private-scraper/1",
                },
            )
            return _OwnedResponse(connection.getresponse(), connection)
        except BoundedTransportError:
            if connection is not None:
                connection.close()
            raise
        except TimeoutError:
            if connection is not None:
                connection.close()
            failure = (
                FailureEvidence.PROXY_CONNECT_TIMEOUT
                if request.route_id is not None
                else FailureEvidence.TRANSPORT_FAILURE
            )
            raise BoundedTransportError(
                "capture transport failed", failure=failure
            ) from None
        except ssl.SSLError:
            if connection is not None:
                connection.close()
            failure = (
                FailureEvidence.PROXY_TLS_FAILURE
                if request.route_id is not None
                else FailureEvidence.TRANSPORT_FAILURE
            )
            raise BoundedTransportError(
                "capture transport failed", failure=failure
            ) from None
        except OSError as error:
            if connection is not None:
                connection.close()
            proxy_authentication = request.route_id is not None and str(
                error
            ).startswith("Tunnel connection failed: 407 ")
            raise BoundedTransportError(
                "capture transport failed",
                failure=(
                    FailureEvidence.PROXY_AUTHENTICATION
                    if proxy_authentication
                    else FailureEvidence.PROXY_CONNECT_TIMEOUT
                    if request.route_id is not None
                    else FailureEvidence.TRANSPORT_FAILURE
                ),
            ) from None
        except Exception:
            if connection is not None:
                connection.close()
            raise BoundedTransportError(
                "capture transport failed",
                failure=FailureEvidence.TRANSPORT_FAILURE,
            ) from None

    def _resolve_proxy(self, route_id: str) -> SplitResult:
        if _ROUTE.fullmatch(route_id) is None:
            raise BoundedTransportError("proxy route unavailable")
        try:
            configured: list[_ProxyRoute] | dict[str, SecretStr] = TypeAdapter(
                list[_ProxyRoute] | dict[str, SecretStr]
            ).validate_json(
                read_secret_text(self._proxy_pool_file)
            )
            if not 1 <= len(configured) <= 64:
                raise ValueError
            if isinstance(configured, dict):
                if any(_ROUTE.fullmatch(key) is None for key in configured):
                    raise ValueError
                proxy_url: SecretStr = configured[route_id]
            else:
                if len({route.route_id for route in configured}) != len(configured):
                    raise ValueError
                proxy_url = next(
                    route.proxy_url
                    for route in configured
                    if route.route_id == route_id
                )
            parsed = urlsplit(proxy_url.get_secret_value())
            if (
                parsed.scheme != "http"
                or parsed.hostname is None
                or parsed.port is None
                or parsed.username is None
                or parsed.password is None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError
            return parsed
        except Exception:
            raise BoundedTransportError("proxy route unavailable") from None


__all__ = ["HttpResponseRequest"]
