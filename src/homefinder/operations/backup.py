"""Encrypted, atomic database backup primitives and retention policy."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"HOMEZ-BACKUP-1\0"
Runner = Callable[..., subprocess.CompletedProcess[bytes]]


def _check_key(key: bytes) -> None:
    if len(key) not in {16, 24, 32}:
        raise ValueError("backup key must be 128, 192, or 256 bits")


def backup_database(
    source: Path | None,
    destination: Path,
    key: bytes,
    *,
    database_url: str | None = None,
    runner: Runner = subprocess.run,
) -> Path:
    _check_key(key)
    if database_url is not None:
        result = runner(
            ["pg_dump", "--format=custom", "--no-password", "--dbname", database_url],
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
) -> None:
    dump = decrypt_backup(path, key)
    runner(
        [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-password",
            "--dbname",
            database_url,
        ],
        input=dump,
        capture_output=True,
        check=True,
    )


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
            removed.append(path)
    return tuple(sorted(removed))
