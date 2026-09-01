import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from homefinder.operations.backup import (
    backup_database,
    decrypt_backup,
    load_backup_key,
    prune_backups,
    restore_database,
)
from homefinder.operations.health import HealthRegistry, HealthState
from homefinder.operations.logging import JsonFormatter, redact


def test_redaction_removes_secrets_from_structured_payload() -> None:
    value = {
        "database_url": "postgresql://user:password@db/homefinder",
        "authorization": "Bearer very-secret-token",
        "nested": ["refresh_token=also-secret", "safe"],
    }

    result = redact(value)

    assert result["database_url"] == "[REDACTED]"
    assert result["authorization"] == "[REDACTED]"
    assert result["nested"] == ["[REDACTED]", "safe"]


def test_json_logging_is_structured_and_redacted() -> None:
    record = logging.LogRecord(
        "homefinder",
        logging.INFO,
        __file__,
        1,
        "backup completed",
        (),
        None,
    )
    record.database_url = "postgresql://u:p@db/x"
    encoded = JsonFormatter().format(record)
    payload = json.loads(encoded)
    assert payload["message"] == "backup completed"
    assert payload["database_url"] == "[REDACTED]"
    assert "postgresql://u:p" not in encoded


def test_health_registry_reports_degraded_component_and_oldest_job() -> None:
    registry = HealthRegistry()
    registry.update(
        "ingestion",
        HealthState.OK,
        checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    registry.update("backup", HealthState.FAIL, detail="NAS unavailable")
    registry.set_job("poll-gmail", datetime(2025, 12, 31, tzinfo=timezone.utc))

    health = registry.snapshot(now=datetime(2026, 1, 2, tzinfo=timezone.utc))

    assert health.status == "degraded"
    assert health.components["backup"].detail == "NAS unavailable"
    assert health.oldest_pending_job == "poll-gmail"


def test_encrypted_backup_round_trip_and_pruning(tmp_path: Path) -> None:
    source = tmp_path / "dump.sql"
    source.write_bytes(b"sensitive database dump")
    destination = tmp_path / "backup.dump.enc"
    key = b"0123456789abcdef0123456789abcdef"

    backup_database(source, destination, key)
    assert destination.read_bytes() != source.read_bytes()
    assert decrypt_backup(destination, key) == source.read_bytes()

    old = tmp_path / "old.dump.enc"
    old.write_bytes(b"old")
    old_time = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    old.touch()
    import os

    os.utime(old, (old_time, old_time))
    assert prune_backups(tmp_path, keep_days=14) == (old,)
    assert not old.exists()


def test_database_backup_and_restore_use_argument_lists(tmp_path: Path) -> None:
    destination = tmp_path / "backup.dump.enc"
    key = b"0123456789abcdef0123456789abcdef"
    calls: list[tuple[list[str], bytes | None, dict[str, str]]] = []

    def runner(  # type: ignore[no-untyped-def]
        command, *, input=None, env, capture_output, check
    ):
        calls.append((command, input, env))
        if command[0] == "pg_dump":
            return type("Result", (), {"stdout": b"dump"})()
        return type("Result", (), {"stdout": b""})()

    backup_database(
        None,
        destination,
        key,
        database_url="postgresql://homefinder:private@db/homefinder",
        runner=runner,
    )
    restore_database(
        destination,
        key,
        database_url="postgresql://homefinder:private@db/homefinder",
        runner=runner,
    )

    assert calls[0][0] == [
        "pg_dump",
        "--no-password",
        "--host",
        "db",
        "--username",
        "homefinder",
        "--dbname",
        "homefinder",
        "--format=custom",
    ]
    assert calls[1][0] == [
        "pg_restore",
        "--no-password",
        "--host",
        "db",
        "--username",
        "homefinder",
        "--dbname",
        "homefinder",
        "--clean",
        "--if-exists",
    ]
    assert calls[1][1] == b"dump"
    assert all("private" not in argument for call in calls for argument in call[0])
    assert calls[0][2]["PGPASSWORD"] == "private"


def test_backup_key_loads_from_private_file(tmp_path: Path) -> None:
    path = tmp_path / "backup-key"
    path.write_text(base64.urlsafe_b64encode(b"k" * 32).decode(), encoding="ascii")
    path.chmod(0o600)

    assert load_backup_key(path) == b"k" * 32
