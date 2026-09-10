"""Encrypted, atomic database backup primitives and retention policy."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine import make_url

from homefinder.sources.gmail import TokenError, read_secret_text

MAGIC = b"HOMEZ-BACKUP-1\0"
Runner = Callable[..., subprocess.CompletedProcess[bytes]]


@dataclass(frozen=True)
class BackupRetentionWarning:
    backup_created_at: datetime
    oldest_production_fetched_at: datetime | None
    live_retention_cutoff: datetime
    blocks_restore: bool = False


def _check_key(key: bytes) -> None:
    if len(key) not in {16, 24, 32}:
        raise ValueError("backup key must be 128, 192, or 256 bits")


def load_backup_key(path: Path) -> bytes:
    try:
        key = base64.urlsafe_b64decode(read_secret_text(path).encode("ascii"))
    except (TokenError, UnicodeError, ValueError) as error:
        raise ValueError("backup key file is invalid") from error
    _check_key(key)
    return key


def backup_database(
    source: Path | None,
    destination: Path,
    key: bytes,
    *,
    database_url: str | None = None,
    runner: Runner = subprocess.run,
    created_at: datetime | None = None,
    oldest_production_fetched_at: datetime | None = None,
) -> Path:
    _check_key(key)
    created = created_at or datetime.now(timezone.utc)
    if created.utcoffset() is None or (
        oldest_production_fetched_at is not None
        and oldest_production_fetched_at.utcoffset() is None
    ):
        raise ValueError("backup manifest timestamps must be timezone-aware")
    if database_url is not None:
        command, environment = _postgres_command("pg_dump", database_url)
        result = runner(
            [*command, "--format=custom"],
            env=environment,
            capture_output=True,
            check=True,
        )
        plaintext = result.stdout
    elif source is not None:
        plaintext = source.read_bytes()
    else:
        raise ValueError("source or database_url is required")
    nonce = token_bytes(12)
    encrypted = MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, MAGIC)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_bytes(encrypted)
    os.chmod(temporary, 0o600)
    temporary.replace(destination)
    os.chmod(destination, 0o600)
    manifest = {
        "schema_version": 1,
        "backup_created_at": created.isoformat(),
        "oldest_production_fetched_at": (
            oldest_production_fetched_at.isoformat()
            if oldest_production_fetched_at is not None
            else None
        ),
        "raw_artifacts_included": False,
    }
    manifest_path = _manifest_path(destination)
    manifest_temporary = manifest_path.with_name(f".{manifest_path.name}.tmp")
    manifest_temporary.write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    os.chmod(manifest_temporary, 0o600)
    manifest_temporary.replace(manifest_path)
    os.chmod(manifest_path, 0o600)
    return destination


def decrypt_backup(path: Path, key: bytes) -> bytes:
    _check_key(key)
    payload = path.read_bytes()
    if not payload.startswith(MAGIC) or len(payload) <= len(MAGIC) + 12:
        raise ValueError("invalid homefinder backup")
    nonce_start = len(MAGIC)
    nonce = payload[nonce_start : nonce_start + 12]
    return AESGCM(key).decrypt(nonce, payload[nonce_start + 12 :], MAGIC)


def restore_database(
    path: Path,
    key: bytes,
    *,
    database_url: str,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
    warn: Callable[[BackupRetentionWarning], None] | None = None,
) -> None:
    if warn is not None:
        warn(inspect_backup_retention(path, now=now))
    dump = decrypt_backup(path, key)
    command, environment = _postgres_command("pg_restore", database_url)
    runner(
        [
            *command,
            "--clean",
            "--if-exists",
        ],
        input=dump,
        env=environment,
        capture_output=True,
        check=True,
    )


def inspect_backup_retention(
    path: Path, *, now: datetime | None = None
) -> BackupRetentionWarning:
    inspected_at = now or datetime.now(timezone.utc)
    if inspected_at.utcoffset() is None:
        raise ValueError("inspection timestamp must be timezone-aware")
    payload = json.loads(_manifest_path(path).read_text(encoding="utf-8"))
    if (
        payload.get("schema_version") != 1
        or payload.get("raw_artifacts_included") is not False
    ):
        raise ValueError("invalid backup manifest")
    created = datetime.fromisoformat(payload["backup_created_at"])
    oldest_value = payload.get("oldest_production_fetched_at")
    oldest = datetime.fromisoformat(oldest_value) if oldest_value is not None else None
    if created.utcoffset() is None or (
        oldest is not None and oldest.utcoffset() is None
    ):
        raise ValueError("invalid backup manifest timestamps")
    try:
        cutoff = inspected_at.replace(year=inspected_at.year - 2)
    except ValueError:
        cutoff = inspected_at.replace(year=inspected_at.year - 2, day=28)
    return BackupRetentionWarning(created, oldest, cutoff)


def _manifest_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.manifest.json")


def _postgres_command(
    program: str, database_url: str
) -> tuple[list[str], dict[str, str]]:
    parsed = make_url(database_url)
    if not parsed.drivername.startswith("postgresql") or not parsed.database:
        raise ValueError("backup requires a PostgreSQL database URL")
    command = [program, "--no-password"]
    for option, value in (
        ("--host", parsed.host),
        ("--port", str(parsed.port) if parsed.port is not None else None),
        ("--username", parsed.username),
        ("--dbname", parsed.database),
    ):
        if value:
            command.extend((option, value))
    environment = os.environ.copy()
    if parsed.password is not None:
        environment["PGPASSWORD"] = parsed.password
    return command, environment


def prune_backups(
    directory: Path, *, keep_days: int, now: datetime | None = None
) -> tuple[Path, ...]:
    if keep_days < 1:
        raise ValueError("keep_days must be positive")
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)
    removed: list[Path] = []
    for path in directory.glob("*.dump.enc"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            path.unlink()
            manifest = _manifest_path(path)
            if manifest.exists():
                manifest.unlink()
            removed.append(path)
    return tuple(sorted(removed))
