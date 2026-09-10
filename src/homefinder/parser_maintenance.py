"""Scoped maintenance authorization contracts for parser evidence."""

import hmac
import json
import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Protocol

from fastapi import FastAPI, Header, HTTPException, Query, Response

RAW_PURPOSES = frozenset({"parser-difference-review", "missing-field-review"})


class MaintenanceAuthorizationError(PermissionError):
    """A maintenance grant is expired or does not match the exact operation."""


@dataclass(frozen=True)
class RawReadGrant:
    subject: str
    artifact_id: str
    purpose: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if (
            not 1 <= len(self.subject) <= 200
            or re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", self.artifact_id) is None
            or self.purpose not in RAW_PURPOSES
            or self.expires_at.utcoffset() is None
        ):
            raise ValueError("invalid raw-read grant")

    def authorize(self, *, artifact_id: str, purpose: str, now: datetime) -> None:
        if (
            now.utcoffset() is None
            or now >= self.expires_at
            or not hmac.compare_digest(self.artifact_id, artifact_id)
            or not hmac.compare_digest(self.purpose, purpose)
        ):
            raise MaintenanceAuthorizationError("raw read is not authorized")


def grant_expiry(now: datetime) -> datetime:
    if now.utcoffset() is None:
        raise ValueError("timezone-aware issue time required")
    return now + timedelta(minutes=30)


class MaintenanceCatalog(Protocol):
    def metadata(
        self, *, cursor: str | None, limit: int
    ) -> tuple[list[dict[str, object]], str | None]: ...

    def clusters(
        self, *, cursor: str | None, limit: int
    ) -> tuple[list[dict[str, object]], str | None]: ...


class ScopedTokenIssuer:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("token signing key must contain at least 32 bytes")
        self._key = key

    def issue(
        self,
        *,
        forced_command: str,
        subject: str,
        artifact_id: str,
        purpose: str,
        now: datetime,
    ) -> str:
        if forced_command != "homez-artifacts issue-token":
            raise MaintenanceAuthorizationError("forced command is not authorized")
        grant = RawReadGrant(subject, artifact_id, purpose, grant_expiry(now))
        body = json.dumps(
            {
                "sub": grant.subject,
                "artifact": grant.artifact_id,
                "purpose": grant.purpose,
                "exp": grant.expires_at.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = hmac.new(self._key, body, sha256).digest()
        encoded_body = urlsafe_b64encode(body).decode()
        encoded_signature = urlsafe_b64encode(signature).decode()
        return f"{encoded_body}.{encoded_signature}"

    def verify(self, token: str) -> RawReadGrant:
        try:
            encoded, supplied = token.split(".", 1)
            body = urlsafe_b64decode(encoded)
            signature = urlsafe_b64decode(supplied)
            if not hmac.compare_digest(
                hmac.new(self._key, body, sha256).digest(), signature
            ):
                raise ValueError
            data = json.loads(body)
            return RawReadGrant(
                data["sub"],
                data["artifact"],
                data["purpose"],
                datetime.fromisoformat(data["exp"]),
            )
        except (KeyError, TypeError, ValueError):
            raise MaintenanceAuthorizationError("invalid maintenance token") from None


def create_maintenance_app(
    catalog: MaintenanceCatalog,
    *,
    issuer: ScopedTokenIssuer,
    read_artifact: Callable[[str], bytes],
    audit: Callable[[str, str, str], None],
    clock: Callable[[], datetime],
) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    def token(authorization: str | None) -> RawReadGrant:
        scheme, _, value = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not value:
            raise HTTPException(401, "Unauthorized")
        try:
            return issuer.verify(value)
        except MaintenanceAuthorizationError:
            raise HTTPException(401, "Unauthorized") from None

    @app.get("/metadata")
    def metadata(
        authorization: str | None = Header(default=None),
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, object]:
        token(authorization)
        items, next_cursor = catalog.metadata(cursor=cursor, limit=limit)
        return {"items": items, "next_cursor": next_cursor}

    @app.get("/clusters")
    def clusters(
        authorization: str | None = Header(default=None),
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, object]:
        token(authorization)
        items, next_cursor = catalog.clusters(cursor=cursor, limit=limit)
        return {"items": items, "next_cursor": next_cursor}

    @app.get("/raw/{artifact_id}")
    def raw(
        artifact_id: str,
        purpose: str,
        authorization: str | None = Header(default=None),
    ) -> Response:
        grant = token(authorization)
        try:
            grant.authorize(artifact_id=artifact_id, purpose=purpose, now=clock())
            audit(grant.subject, artifact_id, purpose)
            body = read_artifact(artifact_id)
        except MaintenanceAuthorizationError:
            raise HTTPException(403, "Forbidden") from None
        except FileNotFoundError:
            raise HTTPException(404, "Artifact unavailable") from None
        except Exception:
            raise HTTPException(503, "Artifact unavailable") from None
        return Response(
            body,
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    return app


__all__ = [
    "MaintenanceAuthorizationError",
    "RawReadGrant",
    "ScopedTokenIssuer",
    "create_maintenance_app",
    "grant_expiry",
]
