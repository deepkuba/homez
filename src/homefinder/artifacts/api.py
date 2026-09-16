"""In-process private artifact API; credentials are supplied by the test harness."""

import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from homefinder.parsers.contracts import MAX_PAGE_BYTES, PageInput, Portal


class ArtifactStore(Protocol):
    def store(self, source: Portal, page: PageInput) -> str: ...

    def read(self, artifact_id: str) -> bytes: ...


@dataclass(frozen=True)
class ArtifactIdentity:
    subject: str
    role: Literal["worker", "maintenance", "benchmark", "recovery"]
    expires_at: datetime
    source: Portal | None = None
    artifact_ids: frozenset[str] = frozenset()
    benchmark_id: str | None = None


@dataclass(frozen=True)
class ArtifactReadAudit:
    subject: str
    artifact_id: str
    attempted_at: datetime
    benchmark_id: str | None


def create_artifact_app(
    store: ArtifactStore,
    *,
    credentials: Mapping[str, ArtifactIdentity],
    audit: Callable[[ArtifactReadAudit], None],
    frozen_manifests: Mapping[str, frozenset[str]] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FastAPI:
    """Freeze supplied scopes and fail closed if audit persistence is unavailable."""
    identities = dict(credentials)
    manifests = {
        key: frozenset(value) for key, value in (frozen_manifests or {}).items()
    }
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def no_cache(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def authenticate(request: Request) -> ArtifactIdentity:
        authorization = request.headers.get("authorization", "")
        scheme, _, supplied = authorization.partition(" ")
        identity = None
        if scheme.lower() == "bearer" and supplied:
            for token, candidate in identities.items():
                if hmac.compare_digest(token.encode(), supplied.encode()):
                    identity = candidate
        if (
            identity is None
            or identity.expires_at.utcoffset() is None
            or identity.expires_at <= clock()
            or (
                identity.role == "recovery"
                and identity.expires_at > clock() + timedelta(minutes=30)
            )
        ):
            raise HTTPException(401, "Unauthorized")
        return identity

    @app.post("/artifacts/{source}", status_code=201)
    async def upload(source: str, request: Request) -> dict[str, str]:
        identity = authenticate(request)
        if (
            identity.role != "worker"
            or source != identity.source
            or identity.source is None
        ):
            raise HTTPException(403, "Forbidden")
        try:
            declared = request.headers.get("content-length")
            if declared is not None and int(declared) > MAX_PAGE_BYTES:
                raise HTTPException(413, "Artifact too large")
            capture_id = UUID(request.headers.get("x-capture-id", ""))
            fetched_at = datetime.fromisoformat(request.headers.get("x-fetched-at", ""))
            if fetched_at.utcoffset() is None:
                raise ValueError("timezone required")
        except ValueError:
            raise HTTPException(400, "Invalid upload metadata") from None
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_PAGE_BYTES:
                raise HTTPException(413, "Artifact too large")
            body.extend(chunk)
        try:
            artifact_id = store.store(
                identity.source, PageInput(capture_id, fetched_at, bytes(body))
            )
        except ValueError:
            raise HTTPException(400, "Invalid artifact") from None
        except Exception:
            raise HTTPException(503, "Artifact unavailable") from None
        return {"artifact_id": artifact_id}

    @app.get("/artifacts/{artifact_id}")
    async def read(artifact_id: str, request: Request) -> Response:
        identity = authenticate(request)
        allowed = (
            (identity.role == "maintenance" and artifact_id in identity.artifact_ids)
            or (
                identity.role == "recovery"
                and identity.source is not None
                and artifact_id in identity.artifact_ids
            )
            or (
                identity.role == "benchmark"
                and identity.benchmark_id is not None
                and artifact_id in identity.artifact_ids
                and artifact_id in manifests.get(identity.benchmark_id, frozenset())
            )
        )
        if not allowed:
            raise HTTPException(403, "Forbidden")
        try:
            audit(
                ArtifactReadAudit(
                    identity.subject, artifact_id, clock(), identity.benchmark_id
                )
            )
        except Exception:
            raise HTTPException(503, "Audit unavailable") from None
        try:
            body = store.read(artifact_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "Artifact unavailable") from None
        except Exception:
            raise HTTPException(503, "Artifact unavailable") from None
        return Response(body, media_type="application/octet-stream")

    return app
