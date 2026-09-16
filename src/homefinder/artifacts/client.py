"""Bounded in-memory writer for the private NAS artifact service."""

import http.client
import ipaddress
import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from homefinder.parsers.contracts import PageInput, Portal
from homefinder.sources.gmail import read_secret_text

ArtifactRequest = Callable[[str, dict[str, str], bytes, str], tuple[int, bytes]]
ArtifactReadRequest = Callable[[str, str], tuple[int, bytes]]
_MAX_RESPONSE_BYTES = 4096
_MAX_ARTIFACT_BYTES = 2_000_000


class ArtifactUnavailable(RuntimeError):
    """Safe artifact boundary failure without raw bytes or credentials."""


class HttpArtifactWriter:
    def __init__(
        self,
        endpoint: str,
        token_file: Path,
        *,
        request: ArtifactRequest | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        try:
            tailnet = ipaddress.ip_address(
                parsed.hostname or ""
            ) in ipaddress.ip_network("100.64.0.0/10")
        except ValueError:
            tailnet = False
        if (
            parsed.scheme not in {"http", "https"}
            or not (tailnet or parsed.hostname == "artifacts")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("artifact endpoint must be private")
        self._host = parsed.hostname or ""
        self._port = parsed.port
        self._https = parsed.scheme == "https"
        self._token_file = token_file
        self._request = request or self._http_request

    def store(self, source: Portal, page: PageInput) -> str:
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(page.body)),
            "X-Capture-Id": str(page.capture_id),
            "X-Fetched-At": page.fetched_at.isoformat(),
        }
        try:
            status, raw = self._request(
                f"/artifacts/{source}",
                headers,
                page.body,
                read_secret_text(self._token_file),
            )
            if status != 201 or len(raw) > _MAX_RESPONSE_BYTES:
                raise ValueError
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"artifact_id"}:
                raise ValueError
            return str(UUID(value["artifact_id"]))
        except Exception:
            raise ArtifactUnavailable("artifact service unavailable") from None

    def _http_request(
        self, path: str, headers: dict[str, str], body: bytes, token: str
    ) -> tuple[int, bytes]:
        connection = (
            http.client.HTTPSConnection(self._host, self._port, timeout=10)
            if self._https
            else http.client.HTTPConnection(self._host, self._port, timeout=10)
        )
        try:
            connection.request(
                "POST",
                path,
                body=body,
                headers={**headers, "Authorization": f"Bearer {token}"},
            )
            response = connection.getresponse()
            return response.status, response.read(_MAX_RESPONSE_BYTES + 1)
        except (OSError, http.client.HTTPException):
            raise ArtifactUnavailable("artifact service unavailable") from None
        finally:
            connection.close()


class HttpArtifactReader:
    def __init__(
        self,
        endpoint: str,
        token_file: Path,
        *,
        request: ArtifactReadRequest | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        try:
            tailnet = ipaddress.ip_address(
                parsed.hostname or ""
            ) in ipaddress.ip_network("100.64.0.0/10")
        except ValueError:
            tailnet = False
        if (
            parsed.scheme not in {"http", "https"}
            or not (tailnet or parsed.hostname == "artifacts")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("artifact endpoint must be private")
        self._host = parsed.hostname or ""
        self._port = parsed.port
        self._https = parsed.scheme == "https"
        self._token_file = token_file
        self._request = request or self._http_request

    def read(self, artifact_id: str) -> bytes:
        try:
            artifact_id = str(UUID(artifact_id))
            status, body = self._request(
                f"/artifacts/{artifact_id}", read_secret_text(self._token_file)
            )
            if status != 200 or not 0 < len(body) <= _MAX_ARTIFACT_BYTES:
                raise ValueError
            return body
        except Exception:
            raise ArtifactUnavailable("artifact service unavailable") from None

    def _http_request(self, path: str, token: str) -> tuple[int, bytes]:
        connection = (
            http.client.HTTPSConnection(self._host, self._port, timeout=10)
            if self._https
            else http.client.HTTPConnection(self._host, self._port, timeout=10)
        )
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Cache-Control": "no-store",
                },
            )
            response = connection.getresponse()
            return response.status, response.read(_MAX_ARTIFACT_BYTES + 1)
        except (OSError, http.client.HTTPException):
            raise ArtifactUnavailable("artifact service unavailable") from None
        finally:
            connection.close()


__all__ = ["ArtifactUnavailable", "HttpArtifactReader", "HttpArtifactWriter"]
