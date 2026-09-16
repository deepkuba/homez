"""Asymmetric, exact-object capabilities for NAS artifact recovery."""

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)

from homefinder.parsers.contracts import Portal
from homefinder.scrape_queue.contracts import (
    ArtifactReplayInput,
    ScrapeLease,
    TaskClass,
    WorkerIdentity,
)
from homefinder.sources.gmail import read_secret_text

_AUDIENCE = "homez-artifact-recovery-v1"
_MAX_LIFETIME = timedelta(minutes=30)


class ArtifactCapabilityError(PermissionError):
    """The signed artifact grant is invalid or does not match the request."""


@dataclass(frozen=True)
class ArtifactCapabilityGrant:
    subject: str
    source: Portal
    artifact_id: str
    task_id: UUID
    activation_epoch: int
    expires_at: datetime


class ArtifactCapabilitySigner:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key

    def issue(
        self,
        *,
        worker: WorkerIdentity,
        lease: ScrapeLease,
        replay: ArtifactReplayInput,
        now: datetime,
    ) -> str:
        if (
            now.utcoffset() is None
            or worker.deployment != "nas"
            or lease.task_class is not TaskClass.ARTIFACT_RECOVERY
            or lease.source != worker.source
            or lease.lease_expires_at <= now
        ):
            raise ArtifactCapabilityError("artifact capability is not authorized")
        expires_at = min(lease.lease_expires_at, now + _MAX_LIFETIME)
        claims = {
            "act": lease.activation_epoch,
            "art": _artifact_id(replay.artifact_id),
            "aud": _AUDIENCE,
            "exp": expires_at.isoformat(),
            "iat": now.isoformat(),
            "lease": hashlib.sha256(lease.lease_token.bytes).hexdigest(),
            "src": lease.source,
            "sub": worker.worker_id,
            "task": str(lease.task_id),
        }
        body = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        return f"{_encode(body)}.{_encode(self._private_key.sign(body))}"


class ArtifactCapabilityVerifier:
    def __init__(self, public_key: Ed25519PublicKey) -> None:
        self._public_key = public_key

    def verify(
        self, token: str, *, artifact_id: str, now: datetime
    ) -> ArtifactCapabilityGrant:
        try:
            if now.utcoffset() is None or len(token) > 4096:
                raise ValueError
            encoded_body, encoded_signature = token.split(".", 1)
            body, signature = _decode(encoded_body), _decode(encoded_signature)
            self._public_key.verify(signature, body)
            claims = json.loads(body)
            if set(claims) != {
                "act",
                "art",
                "aud",
                "exp",
                "iat",
                "lease",
                "src",
                "sub",
                "task",
            }:
                raise ValueError
            issued_at = datetime.fromisoformat(claims["iat"])
            expires_at = datetime.fromisoformat(claims["exp"])
            claimed_artifact = _artifact_id(claims["art"])
            source = claims["src"]
            if (
                claims["aud"] != _AUDIENCE
                or source not in {"gratka", "morizon", "otodom", "olx"}
                or not isinstance(claims["sub"], str)
                or not 1 <= len(claims["sub"]) <= 80
                or not isinstance(claims["act"], int)
                or claims["act"] <= 0
                or not isinstance(claims["lease"], str)
                or len(claims["lease"]) != 64
                or issued_at.utcoffset() is None
                or expires_at.utcoffset() is None
                or issued_at > now
                or expires_at <= now
                or expires_at > issued_at + _MAX_LIFETIME
                or not hmac.compare_digest(claimed_artifact, _artifact_id(artifact_id))
            ):
                raise ValueError
            return ArtifactCapabilityGrant(
                claims["sub"],
                cast(Portal, source),
                claimed_artifact,
                UUID(claims["task"]),
                claims["act"],
                expires_at,
            )
        except (InvalidSignature, KeyError, TypeError, ValueError):
            raise ArtifactCapabilityError("invalid artifact capability") from None


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _artifact_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value) is None
    ):
        raise ValueError("invalid artifact identifier")
    return value


def load_capability_signer(path: Path) -> ArtifactCapabilitySigner:
    try:
        key = load_pem_private_key(read_secret_text(path).encode(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError
        return ArtifactCapabilitySigner(key)
    except Exception:
        raise ValueError("invalid artifact capability signing key") from None


def load_capability_verifier(path: Path) -> ArtifactCapabilityVerifier:
    try:
        key = load_pem_public_key(read_secret_text(path).encode())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError
        return ArtifactCapabilityVerifier(key)
    except Exception:
        raise ValueError("invalid artifact capability verification key") from None


__all__ = [
    "ArtifactCapabilityError",
    "ArtifactCapabilityGrant",
    "ArtifactCapabilitySigner",
    "ArtifactCapabilityVerifier",
    "load_capability_signer",
    "load_capability_verifier",
]
