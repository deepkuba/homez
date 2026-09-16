"""Bounded control-plane client; no database or portal transport capabilities."""

import http.client
import ipaddress
import json
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from homefinder.parsers.contracts import ParserResult
from homefinder.scrape_queue.contracts import (
    ArtifactReplayInput,
    CaptureOutcome,
    LostLease,
    NetworkPermit,
    ScrapeLease,
    WorkerIdentity,
)
from homefinder.scraper.denial_policy import ResponseClassification, RouteDecision
from homefinder.sources.gmail import read_secret_text

ControlRequest = Callable[[str, dict[str, object], str], bytes]
LEASE = TypeAdapter(ScrapeLease)
OUTCOME = TypeAdapter(CaptureOutcome)
PERMIT = TypeAdapter(NetworkPermit)
DECISION = TypeAdapter(RouteDecision)
REPLAY_INPUT = TypeAdapter(ArtifactReplayInput)
PARSER_RESULT = TypeAdapter(ParserResult)
MAX_CONTROL_BYTES = 128_000


class CoordinatorUnavailable(RuntimeError):
    """Safe control-plane failure without server bodies or credentials."""


class HttpCoordinatorClient:
    def __init__(
        self,
        endpoint: str,
        token_file: Path,
        *,
        request: ControlRequest | None = None,
        identity: WorkerIdentity | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        tailnet = False
        with suppress(ValueError):
            tailnet = ipaddress.ip_address(
                parsed.hostname or ""
            ) in ipaddress.ip_network("100.64.0.0/10")
        if (
            parsed.scheme not in {"http", "https"}
            or not (parsed.hostname == "web" or tailnet)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("coordinator must use a private control endpoint")
        self._identity = identity
        self._host = parsed.hostname or ""
        self._port = parsed.port
        self._https = parsed.scheme == "https"
        self._token_file = token_file
        self._request = request or self._http_request

    def _http_request(self, path: str, payload: dict[str, object], token: str) -> bytes:
        connection = (
            http.client.HTTPSConnection(self._host, self._port, timeout=10)
            if self._https
            else http.client.HTTPConnection(self._host, self._port, timeout=10)
        )
        try:
            body = json.dumps(payload).encode()
            if len(body) > MAX_CONTROL_BYTES:
                raise CoordinatorUnavailable("coordinator payload exceeds bound")
            connection.request(
                "POST",
                path,
                body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            if response.status == 409:
                raise LostLease("coordinator rejected lease")
            if response.status != 200:
                raise CoordinatorUnavailable("coordinator request failed")
            raw = response.read(MAX_CONTROL_BYTES + 1)
            if len(raw) > MAX_CONTROL_BYTES:
                raise CoordinatorUnavailable("coordinator response exceeds bound")
            return raw
        except (OSError, http.client.HTTPException):
            raise CoordinatorUnavailable("coordinator unavailable") from None
        finally:
            connection.close()

    def _call(self, operation: str, payload: dict[str, object]) -> bytes:
        try:
            raw = self._request(
                "/internal/scrape/v1/" + operation,
                payload,
                read_secret_text(self._token_file),
            )
            if len(raw) > MAX_CONTROL_BYTES:
                raise CoordinatorUnavailable("coordinator response exceeds bound")
            return raw
        except ValueError:
            raise CoordinatorUnavailable("coordinator credential unavailable") from None

    def register(self, releases: tuple[str, ...], healthy: bool = True) -> None:
        payload: dict[str, object] = {
            "release_hashes": list(releases),
            "healthy": healthy,
        }
        if self._identity is not None:
            payload["expected_identity"] = TypeAdapter(WorkerIdentity).dump_python(
                self._identity, mode="json"
            )
        self._call("workers/heartbeat", payload)

    def claim(self) -> ScrapeLease | None:
        raw = self._call("claim", {"task_classes": ["live", "network_recovery"]})
        if raw == b"null":
            return None
        try:
            return LEASE.validate_json(raw)
        except ValueError:
            raise CoordinatorUnavailable("invalid coordinator lease") from None

    def claim_artifact_recovery(self) -> ScrapeLease | None:
        raw = self._call("artifact/claim", {})
        if raw == b"null":
            return None
        try:
            return LEASE.validate_json(raw)
        except ValueError:
            raise CoordinatorUnavailable("invalid coordinator lease") from None

    def replay_input(self, lease: ScrapeLease) -> ArtifactReplayInput:
        raw = self._call(
            "artifact/input", {"lease": LEASE.dump_python(lease, mode="json")}
        )
        try:
            return REPLAY_INPUT.validate_json(raw)
        except ValueError:
            raise CoordinatorUnavailable("invalid artifact replay input") from None

    def complete_artifact_replay(
        self, lease: ScrapeLease, result: ParserResult
    ) -> None:
        self._call(
            "artifact/complete",
            {
                "lease": LEASE.dump_python(lease, mode="json"),
                "result": PARSER_RESULT.dump_python(result, mode="json"),
            },
        )

    def heartbeat(self, lease: ScrapeLease) -> ScrapeLease:
        raw = self._call("heartbeat", {"lease": LEASE.dump_python(lease, mode="json")})
        try:
            return LEASE.validate_json(raw)
        except ValueError:
            raise CoordinatorUnavailable("invalid coordinator lease") from None

    def reserve_start(self, lease: ScrapeLease) -> NetworkPermit:
        raw = self._call(
            "network/reserve",
            {
                "lease": LEASE.dump_python(lease, mode="json"),
                "requested_proxy_bytes": 2_000_000,
            },
        )
        try:
            return PERMIT.validate_json(raw)
        except ValueError:
            raise CoordinatorUnavailable("invalid coordinator permit") from None

    def defer(self, lease: ScrapeLease, available_at: datetime, code: str) -> None:
        self._call(
            "defer",
            {
                "lease": LEASE.dump_python(lease, mode="json"),
                "available_at": available_at.isoformat(),
                "code": code,
            },
        )

    def record_network_outcome(
        self,
        lease: ScrapeLease,
        permit: NetworkPermit,
        classification: ResponseClassification,
        transferred_bytes: int,
        retry_after_seconds: int | None = None,
    ) -> RouteDecision:
        raw = self._call(
            "network/outcome",
            {
                "lease": LEASE.dump_python(lease, mode="json"),
                "permit": PERMIT.dump_python(permit, mode="json"),
                "classification": classification.value,
                "transferred_bytes": transferred_bytes,
                "retry_after_seconds": retry_after_seconds,
            },
        )
        try:
            return DECISION.validate_json(raw)
        except ValueError:
            raise CoordinatorUnavailable("invalid coordinator decision") from None

    def complete(self, lease: ScrapeLease, outcome: CaptureOutcome) -> None:
        self._call(
            "complete",
            {
                "lease": LEASE.dump_python(lease, mode="json"),
                "outcome": OUTCOME.dump_python(outcome, mode="json"),
            },
        )

    def fail(self, lease: ScrapeLease, code: str) -> None:
        self._call(
            "fail", {"lease": LEASE.dump_python(lease, mode="json"), "code": code}
        )
