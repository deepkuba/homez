from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from homefinder.parsers.contracts import MAX_PAGE_BYTES, PageInput

NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)
KEY = bytes(range(32))
BODY = b"<html><p>Synthetic example</p></html>\x00\xff"


def page(body=BODY, fetched_at=NOW):
    return PageInput(uuid4(), fetched_at, body)


def new_store(path, clock=lambda: NOW):
    from homefinder.artifacts.store import EncryptedArtifactStore

    return EncryptedArtifactStore(path, wrapping_key=KEY, clock=clock)


def test_store_encrypts_exact_parser_bytes_with_wrapped_per_object_keys(tmp_path):
    store = new_store(tmp_path)
    artifact_id = store.store("gratka", page())
    assert store.read(artifact_id) == BODY
    metadata = store.metadata(artifact_id)
    assert metadata.byte_length == len(BODY)
    assert metadata.expires_at == NOW + timedelta(days=30)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert BODY not in path.read_bytes()
            assert KEY not in path.read_bytes()


def test_dedupe_is_per_source_and_does_not_extend_retention(tmp_path):
    store = new_store(tmp_path)
    artifact_id = store.store("gratka", page(fetched_at=NOW - timedelta(days=2)))
    assert store.store("gratka", page()) == artifact_id
    assert store.metadata(artifact_id).expires_at == NOW + timedelta(days=28)
    assert store.store("olx", page()) != artifact_id


def test_concurrent_writers_share_one_encrypted_object(tmp_path):
    def write(_):
        return new_store(tmp_path).store("gratka", page())

    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(write, range(12)))
    assert len(set(ids)) == 1
    assert len(list((tmp_path / "objects").glob("*.enc"))) == 1


def test_expired_read_refuses_immediately_and_crypto_shreds(tmp_path):
    now = [NOW]
    store = new_store(tmp_path, lambda: now[0])
    artifact_id = store.store("gratka", page())
    now[0] += timedelta(days=30)
    with pytest.raises(FileNotFoundError):
        store.read(artifact_id)
    assert list((tmp_path / "objects").glob("*.enc")) == []
    assert store.tombstone(artifact_id).reason == "expired"


def test_manual_delete_is_idempotent_and_retains_safe_tombstone(tmp_path):
    store = new_store(tmp_path)
    artifact_id = store.store("gratka", page())
    first = store.delete(artifact_id)
    assert first == store.delete(artifact_id)
    assert first.reason == "manual"
    assert BODY.decode("latin1") not in repr(first)
    assert not list((tmp_path / "objects").glob("*.enc"))
    with pytest.raises(FileNotFoundError):
        store.read(artifact_id)


def test_tampered_ciphertext_and_wrong_wrapping_key_fail_closed(tmp_path):
    from homefinder.artifacts.store import (
        ArtifactIntegrityError,
        EncryptedArtifactStore,
    )

    store = new_store(tmp_path)
    artifact_id = store.store("gratka", page())
    wrong_key_store = EncryptedArtifactStore(
        tmp_path, wrapping_key=b"z" * 32, clock=lambda: NOW
    )
    with pytest.raises(ArtifactIntegrityError):
        wrong_key_store.read(artifact_id)
    path = next((tmp_path / "objects").glob("*.enc"))
    ciphertext = path.read_bytes()
    path.write_bytes(ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]))
    with pytest.raises(ArtifactIntegrityError):
        store.read(artifact_id)


def test_expiry_sweep_and_older_duplicate_preserve_original_deadline(tmp_path):
    now = [NOW]
    store = new_store(tmp_path, lambda: now[0])
    artifact_id = store.store("gratka", page())
    store.store("gratka", page(fetched_at=NOW - timedelta(days=1)))
    assert store.metadata(artifact_id).expires_at == NOW + timedelta(days=29)
    now[0] += timedelta(days=29)
    assert store.expire() == (artifact_id,)
    assert store.expire() == ()


def test_store_rejects_expired_input_and_unsafe_identifiers(tmp_path):
    store = new_store(tmp_path)
    with pytest.raises(ValueError):
        store.store("gratka", page(fetched_at=NOW - timedelta(days=30)))
    with pytest.raises(ValueError):
        store.read("../../outside")
    with pytest.raises(ValueError):
        store.delete("../../outside")


def test_input_byte_limit_is_exact(tmp_path):
    store = new_store(tmp_path)
    body = b"s" * MAX_PAGE_BYTES
    assert store.read(store.store("gratka", page(body))) == body
    with pytest.raises(ValueError, match="2 MB"):
        page(body + b"s")


def test_new_capture_after_expiry_gets_new_artifact_without_resurrection(tmp_path):
    now = [NOW]
    store = new_store(tmp_path, lambda: now[0])
    old_id = store.store("gratka", page())
    now[0] += timedelta(days=30)
    new_id = store.store("gratka", page(fetched_at=now[0]))
    assert new_id != old_id
    assert store.tombstone(old_id).reason == "expired"
    assert store.read(new_id) == BODY


def test_each_object_uses_a_fresh_data_key_never_persisted_plaintext(tmp_path):
    import sqlite3

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    store = new_store(tmp_path)
    store.store("gratka", page())
    store.store("olx", page())
    with sqlite3.connect(tmp_path / "metadata.sqlite3") as connection:
        rows = connection.execute(
            "SELECT artifact_id, source, content_hash, wrap_nonce, wrapped_key "
            "FROM artifacts"
        ).fetchall()
    keys = [
        AESGCM(KEY).decrypt(
            row[3], row[4], f"homez-artifact-v1:{row[0]}:{row[1]}:{row[2]}".encode()
        )
        for row in rows
    ]
    assert keys[0] != keys[1]
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert all(key not in path.read_bytes() for key in keys)


def test_delete_retry_removes_ciphertext_after_unlink_failure(tmp_path, monkeypatch):
    from pathlib import Path

    store = new_store(tmp_path)
    artifact_id = store.store("gratka", page())
    original_unlink = Path.unlink

    def fail_unlink(self, missing_ok=False):
        raise OSError("synthetic disk error")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(OSError):
        store.delete(artifact_id)
    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert store.delete(artifact_id).reason == "manual"
    assert not list((tmp_path / "objects").glob("*.enc"))


def test_concurrent_refetch_after_expiry_dedupes(tmp_path):
    store = new_store(tmp_path)
    store.store("gratka", page())
    later = NOW + timedelta(days=30)

    def write(_):
        return new_store(tmp_path, lambda: later).store(
            "gratka", page(fetched_at=later)
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(write, range(12)))
    assert len(set(ids)) == 1
    assert len(list((tmp_path / "objects").glob("*.enc"))) == 1


def test_positive_reservoir_bounds_each_variant_and_prefers_structural_diversity(
    tmp_path,
):
    store = new_store(tmp_path)
    for index in range(5):
        assert store.store_quality_sample(
            "gratka",
            page(f"synthetic-{index}".encode()),
            variant="v1",
            structure_hash=f"{index:064x}",
        )
    assert (
        store.store_quality_sample(
            "gratka",
            page(b"synthetic-over-limit"),
            variant="v1",
            structure_hash="f" * 64,
        )
        is None
    )
    assert store.store_quality_sample(
        "gratka",
        page(b"synthetic-new-variant"),
        variant="v2",
        structure_hash="f" * 64,
    )
    assert (
        store.store_quality_sample(
            "gratka",
            page(b"synthetic-same-structure"),
            variant="v2",
            structure_hash="f" * 64,
        )
        is None
    )
    assert len(list((tmp_path / "objects").glob("*.enc"))) == 6


def test_correct_positive_review_shreds_and_frees_slot(tmp_path):
    store = new_store(tmp_path)
    artifact_id = store.store_quality_sample(
        "gratka",
        page(),
        variant="v1",
        structure_hash="a" * 64,
    )
    store.review_quality_sample(artifact_id, correct=True)
    assert store.tombstone(artifact_id).reason == "quality-confirmed"
    assert store.store_quality_sample(
        "gratka",
        page(b"synthetic-replacement"),
        variant="v1",
        structure_hash="a" * 64,
    )


def test_incorrect_positive_review_reclassifies_without_rewriting_bytes(tmp_path):
    store = new_store(tmp_path)
    artifact_id = store.store_quality_sample(
        "gratka",
        page(),
        variant="v1",
        structure_hash="a" * 64,
    )
    store.review_quality_sample(artifact_id, correct=False)
    assert store.metadata(artifact_id).kind == "diagnostic"
    assert store.read(artifact_id) == BODY
    assert store.tombstone(artifact_id) is None


def test_diagnostics_reuse_samples_and_are_never_deleted_by_positive_review(tmp_path):
    store = new_store(tmp_path)
    sample_id = store.store_quality_sample(
        "gratka",
        page(),
        variant="v1",
        structure_hash="a" * 64,
    )
    assert store.store("gratka", page()) == sample_id
    store.review_quality_sample(sample_id, correct=True)
    assert store.read(sample_id) == BODY
    assert store.metadata(sample_id).kind == "diagnostic"
    assert (
        store.store_quality_sample(
            "gratka",
            page(),
            variant="v1",
            structure_hash="a" * 64,
        )
        == sample_id
    )
    assert len(list((tmp_path / "objects").glob("*.enc"))) == 1


def test_concurrent_quality_samples_never_exceed_five(tmp_path):
    def write(index):
        return new_store(tmp_path).store_quality_sample(
            "gratka",
            page(f"synthetic-{index}".encode()),
            variant="v1",
            structure_hash=f"{index:064x}",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(write, range(12)))
    assert sum(result is not None for result in results) == 5
    assert len(list((tmp_path / "objects").glob("*.enc"))) == 5


def test_expiry_sweep_retries_ciphertext_unlink_after_key_was_shredded(
    tmp_path, monkeypatch
):
    from pathlib import Path

    now = [NOW]
    store = new_store(tmp_path, lambda: now[0])
    artifact_id = store.store("gratka", page())
    now[0] += timedelta(days=30)
    original_unlink = Path.unlink

    def fail_unlink(self, missing_ok=False):
        raise OSError("synthetic disk error")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(OSError):
        store.expire()
    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert store.expire() == ()
    assert store.tombstone(artifact_id).reason == "expired"
    assert not list((tmp_path / "objects").glob("*.enc"))
