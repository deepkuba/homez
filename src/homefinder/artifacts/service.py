"""Explicit NAS-only service bootstrap; secrets are read from private files."""

import argparse
import asyncio
import base64
import json
import os
import re
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import uvicorn
from fastapi import FastAPI

from homefinder.artifact_capability import load_capability_verifier
from homefinder.artifacts.api import (
    ArtifactIdentity,
    ArtifactReadAudit,
    create_artifact_app,
)
from homefinder.artifacts.store import EncryptedArtifactStore
from homefinder.parsers.contracts import Portal


class ServiceConfigurationError(ValueError):
    """Safe configuration failure without secret values or file contents."""


def _private(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    valid_kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        not valid_kind
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("private path required")


def _read_private(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > 1_000_000
        ):
            raise ValueError("private secret required")
        value = stream.read(1_000_001)
        if len(value) > 1_000_000:
            raise ValueError("secret configuration too large")
        return value


def _safe_name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid scope identifier")
    return value


def _audit_writer(path: Path, event: ArtifactReadAudit | None = None) -> None:
    _private(path.parent, directory=True)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise ValueError("private audit required")
        if event is not None:
            record = (
                json.dumps(
                    {
                        "subject": _safe_name(event.subject),
                        "artifact_id": _safe_name(event.artifact_id),
                        "attempted_at": event.attempted_at.isoformat(),
                        "benchmark_id": event.benchmark_id,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
            payload = record.encode()
            if os.write(descriptor, payload) != len(payload):
                raise OSError("audit write failed")
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build_app(
    *,
    root: Path,
    wrapping_key_file: Path,
    credentials_file: Path,
    audit_file: Path,
    capability_public_key_file: Path | None = None,
    retention_interval_seconds: int = 3600,
) -> FastAPI:
    try:
        if not 1 <= retention_interval_seconds <= 3600:
            raise ValueError("hourly retention required")
        _private(root, directory=True)
        _private(wrapping_key_file)
        _private(credentials_file)
        key = base64.b64decode(_read_private(wrapping_key_file).strip(), validate=True)
        if len(key) != 32:
            raise ValueError("invalid key")
        config = json.loads(_read_private(credentials_file))
        if not isinstance(config, dict) or set(config) - {
            "credentials",
            "frozen_manifests",
        }:
            raise ValueError("invalid configuration")
        manifests = {}
        for name, members in config.get("frozen_manifests", {}).items():
            if not isinstance(members, list) or len(members) > 1000:
                raise ValueError("invalid manifest")
            manifests[_safe_name(name)] = frozenset(
                _safe_name(item) for item in members
            )
        identities = {}
        now = datetime.now(timezone.utc)
        for token, raw in config["credentials"].items():
            if (
                not isinstance(token, str)
                or not token
                or len(token) > 4096
                or any(char.isspace() for char in token)
            ):
                raise ValueError("invalid credential")
            if set(raw) - {
                "subject",
                "role",
                "expires_at",
                "source",
                "artifact_ids",
                "benchmark_id",
            }:
                raise ValueError("invalid identity")
            subject = _safe_name(raw["subject"])
            expiry = datetime.fromisoformat(raw["expires_at"])
            if expiry.utcoffset() is None or expiry <= now:
                raise ValueError("expired identity")
            role = raw["role"]
            ids = raw.get("artifact_ids", [])
            if not isinstance(ids, list):
                raise ValueError("invalid scope")
            members = frozenset(_safe_name(item) for item in ids)
            if role == "worker":
                if (
                    raw.get("source") not in {"olx", "otodom", "morizon", "gratka"}
                    or members
                    or raw.get("benchmark_id") is not None
                ):
                    raise ValueError("invalid worker scope")
                identity = ArtifactIdentity(
                    subject, "worker", expiry, source=cast(Portal, raw["source"])
                )
            elif role == "maintenance":
                if (
                    not members
                    or raw.get("source") is not None
                    or raw.get("benchmark_id") is not None
                ):
                    raise ValueError("invalid maintenance scope")
                identity = ArtifactIdentity(
                    subject, "maintenance", expiry, artifact_ids=members
                )
            elif role == "benchmark":
                benchmark = _safe_name(raw.get("benchmark_id"))
                if (
                    benchmark not in manifests
                    or members
                    or raw.get("source") is not None
                ):
                    raise ValueError("invalid benchmark scope")
                identity = ArtifactIdentity(
                    subject, "benchmark", expiry, benchmark_id=benchmark
                )
            else:
                raise ValueError("invalid role")
            identities[token] = identity
        if not identities:
            raise ValueError("credentials required")
        _audit_writer(audit_file)
        store = EncryptedArtifactStore(root, wrapping_key=key)
        app = create_artifact_app(
            store,
            credentials=identities,
            frozen_manifests=manifests,
            capability_verifier=(
                load_capability_verifier(capability_public_key_file)
                if capability_public_key_file is not None
                else None
            ),
            audit=lambda event: _audit_writer(audit_file, event),
        )
    except Exception:
        raise ServiceConfigurationError(
            "Invalid artifact service configuration"
        ) from None

    async def expire_periodically() -> None:
        while True:
            await asyncio.sleep(retention_interval_seconds)
            try:
                await asyncio.to_thread(store.expire)
            except Exception:  # noqa: S112 - do not log raw-derived exceptions
                # Reads still enforce expiry. Retry next interval without secret logs.
                continue

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(store.expire)
        task = asyncio.create_task(expire_periodically())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app.router.lifespan_context = lifespan
    app.state.artifact_store = store
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Private NAS artifact service")
    parser.add_argument("command", choices=("serve", "expire-once"))
    for name in ("root", "wrapping-key-file", "credentials-file", "audit-file"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--capability-public-key-file", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--retention-interval-seconds", type=int, default=3600)
    args = parser.parse_args(argv)
    try:
        app = build_app(
            root=args.root,
            wrapping_key_file=args.wrapping_key_file,
            credentials_file=args.credentials_file,
            audit_file=args.audit_file,
            capability_public_key_file=args.capability_public_key_file,
            retention_interval_seconds=args.retention_interval_seconds,
        )
        if args.command == "expire-once":
            app.state.artifact_store.expire()
        else:
            uvicorn.run(app, host=args.host, port=args.port, access_log=False)
    except Exception:
        print("Artifact service unavailable")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
