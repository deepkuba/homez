"""Encrypted, atomic database backup primitives and retention policy."""

from __future__ import annotations

import base64
import os
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_bytes

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine import make_url

from homefinder.sources.gmail import TokenError, read_secret_text

MAGIC = b"HOMEZ-BACKUP-1\0"
Runner = Callable[..., subprocess.CompletedProcess[bytes]]


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
) -> Path:
    _check_key(key)
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
            removed.append(path)
    return tuple(sorted(removed))
