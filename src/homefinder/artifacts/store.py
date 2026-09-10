"""NAS-local encrypted objects and safe metadata; never persist parser plaintext.

The wrapping key is supplied in memory by the NAS service. This directory and
its key source must be excluded from backups. SQLite serializes deduplication
across independent upload processes; ciphertext is never stored in SQLite.
"""

import hashlib
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from homefinder.parsers.contracts import MAX_PAGE_BYTES, PageInput, Portal

RETENTION = timedelta(days=30)
DeletionReason = Literal["manual", "expired", "quality-confirmed"]


class ArtifactIntegrityError(ValueError):
    """An object cannot be authenticated; no content is returned."""


@dataclass(frozen=True)
class ArtifactMetadata:
    artifact_id: str
    source: str
    content_hash: str
    byte_length: int
    fetched_at: datetime
    expires_at: datetime
    kind: str = "diagnostic"
    variant: str | None = None


@dataclass(frozen=True)
class ArtifactTombstone:
    artifact_id: str
    source: str
    content_hash: str
    deleted_at: datetime
    reason: str


class EncryptedArtifactStore:
    def __init__(
        self,
        root: Path,
        *,
        wrapping_key: bytes,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if len(wrapping_key) != 32:
            raise ValueError("NAS wrapping key must contain 32 bytes")
        self._wrapper = AESGCM(wrapping_key)
        self._clock = clock
        self._root = root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._objects = root / "objects"
        self._objects.mkdir(mode=0o700, exist_ok=True)
        if root.is_symlink() or self._objects.is_symlink():
            raise ValueError("artifact storage must not use symbolic links")
        self._database = root / "metadata.sqlite3"
        descriptor = os.open(
            self._database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        os.close(descriptor)
        with self._transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS artifacts ("
                "artifact_id TEXT PRIMARY KEY, source TEXT NOT NULL, "
                "content_hash TEXT NOT NULL, byte_length INTEGER NOT NULL, "
                "fetched_at TEXT NOT NULL, expires_at TEXT NOT NULL, "
                "nonce BLOB NOT NULL, wrap_nonce BLOB NOT NULL, "
                "wrapped_key BLOB NOT NULL, kind TEXT NOT NULL, "
                "variant TEXT, structure_hash TEXT, UNIQUE(source, content_hash))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS tombstones ("
                "artifact_id TEXT PRIMARY KEY, source TEXT NOT NULL, "
                "content_hash TEXT NOT NULL, deleted_at TEXT NOT NULL, "
                "reason TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS quality_reviews ("
                "artifact_id TEXT NOT NULL, correct INTEGER NOT NULL, "
                "reviewed_at TEXT NOT NULL)"
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _now(self) -> datetime:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("artifact clock must be timezone-aware")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _validate_id(artifact_id: str) -> None:
        if str(UUID(artifact_id)) != artifact_id:
            raise ValueError("invalid artifact identifier")

    @staticmethod
    def _aad(artifact_id: str, source: str, content_hash: str) -> bytes:
        return f"homez-artifact-v1:{artifact_id}:{source}:{content_hash}".encode()

    def store(self, source: Portal, page: PageInput) -> str:
        result = self._store(source, page)
        if result is None:
            raise RuntimeError("diagnostic storage did not return an identifier")
        return result

    def store_quality_sample(
        self, source: Portal, page: PageInput, *, variant: str, structure_hash: str
    ) -> str | None:
        """Retain at most five unreviewed, structurally distinct samples per variant."""
        if (
            not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", variant)
            or variant == "unknown-variant"
        ):
            raise ValueError("quality sample requires a detected variant")
        if not re.fullmatch(r"[a-f0-9]{64}", structure_hash):
            raise ValueError("invalid structural fingerprint")
        return self._store(source, page, variant=variant, structure_hash=structure_hash)

    def _store(
        self,
        source: Portal,
        page: PageInput,
        *,
        variant: str | None = None,
        structure_hash: str | None = None,
    ) -> str | None:
        if source not in {"gratka", "morizon", "otodom", "olx"}:
            raise ValueError("unsupported artifact source")
        if len(page.body) > MAX_PAGE_BYTES:
            raise ValueError("artifact input exceeds 2 MB")
        fetched_at = page.fetched_at.astimezone(timezone.utc)
        expires_at = fetched_at + RETENTION
        now = self._now()
        if expires_at <= now:
            raise ValueError("artifact input has already expired")
        if fetched_at > now:
            raise ValueError("artifact capture is in the future")
        if variant is not None:
            self.expire()
        content_hash = hashlib.sha256(page.body).hexdigest()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM artifacts WHERE source=? AND content_hash=?",
                (source, content_hash),
            ).fetchone()
            while (
                existing is not None
                and datetime.fromisoformat(existing["expires_at"]) <= now
            ):
                self._shred(connection, existing, now, "expired")
                # Shredding commits key removal. Another uploader may have
                # populated this hash while the transaction was released.
                existing = connection.execute(
                    "SELECT * FROM artifacts WHERE source=? AND content_hash=?",
                    (source, content_hash),
                ).fetchone()
            if existing is not None:
                if variant is None:
                    connection.execute(
                        "UPDATE artifacts SET kind='diagnostic', variant=NULL, "
                        "structure_hash=NULL WHERE artifact_id=?",
                        (existing["artifact_id"],),
                    )
                if expires_at < datetime.fromisoformat(existing["expires_at"]):
                    connection.execute(
                        "UPDATE artifacts SET fetched_at=?, expires_at=? "
                        "WHERE artifact_id=?",
                        (
                            fetched_at.isoformat(),
                            expires_at.isoformat(),
                            existing["artifact_id"],
                        ),
                    )
                return str(existing["artifact_id"])
            if variant is not None:
                samples = connection.execute(
                    "SELECT structure_hash FROM artifacts WHERE source=? "
                    "AND variant=? AND kind='quality-sample'",
                    (source, variant),
                ).fetchall()
                if len(samples) >= 5 or any(
                    row["structure_hash"] == structure_hash for row in samples
                ):
                    return None
            artifact_id = str(uuid4())
            nonce = os.urandom(12)
            wrap_nonce = os.urandom(12)
            data_key = AESGCM.generate_key(bit_length=256)
            aad = self._aad(artifact_id, source, content_hash)
            ciphertext = AESGCM(data_key).encrypt(nonce, page.body, aad)
            wrapped_key = self._wrapper.encrypt(wrap_nonce, data_key, aad)
            del data_key
            path = self._objects / f"{artifact_id}.enc"
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(ciphertext)
                    output.flush()
                    os.fsync(output.fileno())
                connection.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        artifact_id,
                        source,
                        content_hash,
                        len(page.body),
                        fetched_at.isoformat(),
                        expires_at.isoformat(),
                        nonce,
                        wrap_nonce,
                        wrapped_key,
                        "diagnostic" if variant is None else "quality-sample",
                        variant,
                        structure_hash,
                    ),
                )
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        return artifact_id

    def _shred(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        now: datetime,
        reason: DeletionReason,
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO tombstones VALUES (?, ?, ?, ?, ?)",
            (
                row["artifact_id"],
                row["source"],
                row["content_hash"],
                now.isoformat(),
                reason,
            ),
        )
        connection.execute(
            "DELETE FROM artifacts WHERE artifact_id=?", (row["artifact_id"],)
        )
        # Commit deletion of the wrapped key before unlinking its ciphertext.
        connection.commit()
        (self._objects / f"{row['artifact_id']}.enc").unlink(missing_ok=True)
        connection.execute("BEGIN IMMEDIATE")

    def read(self, artifact_id: str) -> bytes:
        self._validate_id(artifact_id)
        with self._transaction() as connection:
            row = self._live_row(connection, artifact_id)
            aad = self._aad(artifact_id, row["source"], row["content_hash"])
            try:
                descriptor = os.open(
                    self._objects / f"{artifact_id}.enc", os.O_RDONLY | os.O_NOFOLLOW
                )
                with os.fdopen(descriptor, "rb") as source:
                    ciphertext = source.read(MAX_PAGE_BYTES + 17)
                if len(ciphertext) > MAX_PAGE_BYTES + 16:
                    raise ArtifactIntegrityError("artifact authentication failed")
                data_key = self._wrapper.decrypt(
                    row["wrap_nonce"], row["wrapped_key"], aad
                )
                body = AESGCM(data_key).decrypt(row["nonce"], ciphertext, aad)
                del data_key
            except (InvalidTag, ValueError) as exc:
                raise ArtifactIntegrityError("artifact authentication failed") from exc
            if (
                len(body) != row["byte_length"]
                or hashlib.sha256(body).hexdigest() != row["content_hash"]
            ):
                raise ArtifactIntegrityError("artifact authentication failed")
            return body

    def _live_row(
        self, connection: sqlite3.Connection, artifact_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise FileNotFoundError("artifact unavailable")
        now = self._now()
        if datetime.fromisoformat(row["expires_at"]) <= now:
            self._shred(connection, row, now, "expired")
            raise FileNotFoundError("artifact unavailable")
        return cast(sqlite3.Row, row)

    def metadata(self, artifact_id: str) -> ArtifactMetadata:
        self._validate_id(artifact_id)
        with self._transaction() as connection:
            row = self._live_row(connection, artifact_id)
            return ArtifactMetadata(
                artifact_id,
                row["source"],
                row["content_hash"],
                row["byte_length"],
                datetime.fromisoformat(row["fetched_at"]),
                datetime.fromisoformat(row["expires_at"]),
                row["kind"],
                row["variant"],
            )

    def review_quality_sample(self, artifact_id: str, *, correct: bool) -> None:
        self._validate_id(artifact_id)
        with self._transaction() as connection:
            row = self._live_row(connection, artifact_id)
            connection.execute(
                "INSERT INTO quality_reviews VALUES (?, ?, ?)",
                (artifact_id, int(correct), self._now().isoformat()),
            )
            if row["kind"] != "quality-sample":
                return
            if correct:
                self._shred(connection, row, self._now(), "quality-confirmed")
            else:
                connection.execute(
                    "UPDATE artifacts SET kind='diagnostic', variant=NULL, "
                    "structure_hash=NULL WHERE artifact_id=?",
                    (artifact_id,),
                )

    def delete(
        self, artifact_id: str, *, reason: DeletionReason = "manual"
    ) -> ArtifactTombstone:
        self._validate_id(artifact_id)
        if reason not in {"manual", "expired", "quality-confirmed"}:
            raise ValueError("unsupported artifact deletion reason")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            if row is not None:
                self._shred(connection, row, self._now(), reason)
        tombstone = self.tombstone(artifact_id)
        if tombstone is None:
            raise FileNotFoundError("artifact unavailable")
        # A prior interruption may have removed the key but left ciphertext.
        (self._objects / f"{artifact_id}.enc").unlink(missing_ok=True)
        return tombstone

    def tombstone(self, artifact_id: str) -> ArtifactTombstone | None:
        self._validate_id(artifact_id)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM tombstones WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            return (
                None
                if row is None
                else ArtifactTombstone(
                    artifact_id,
                    row["source"],
                    row["content_hash"],
                    datetime.fromisoformat(row["deleted_at"]),
                    row["reason"],
                )
            )

    def expire(self) -> tuple[str, ...]:
        now = self._now()
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE expires_at <= ? ORDER BY artifact_id",
                (now.isoformat(),),
            ).fetchall()
            for row in rows:
                self._shred(connection, row, now, "expired")
            for tombstone in connection.execute("SELECT artifact_id FROM tombstones"):
                (self._objects / f"{tombstone['artifact_id']}.enc").unlink(
                    missing_ok=True
                )
            return tuple(str(row["artifact_id"]) for row in rows)
