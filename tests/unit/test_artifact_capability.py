from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


def test_signed_recovery_capability_is_exact_tamper_proof_and_lease_bounded():
    from homefinder.artifact_capability import (
        ArtifactCapabilityError,
        ArtifactCapabilitySigner,
        ArtifactCapabilityVerifier,
    )
    from homefinder.scrape_queue.contracts import (
        ArtifactReplayInput,
        ScrapeLease,
        TaskClass,
        WorkerIdentity,
    )

    private_key = Ed25519PrivateKey.generate()
    signer = ArtifactCapabilitySigner(private_key)
    verifier = ArtifactCapabilityVerifier(private_key.public_key())
    worker = WorkerIdentity("recovery-nas-gratka", "gratka", "nas")
    lease = ScrapeLease(
        uuid4(),
        "gratka",
        uuid4(),
        "https://gratka.pl/nieruchomosci/test/ob/10000001",
        TaskClass.ARTIFACT_RECOVERY,
        "a" * 64,
        3,
        uuid4(),
        NOW + timedelta(minutes=2),
        1,
    )
    replay = ArtifactReplayInput(
        uuid4(),
        str(uuid4()),
        NOW - timedelta(days=1),
        "b" * 64,
        NOW + timedelta(days=20),
    )

    token = signer.issue(worker=worker, lease=lease, replay=replay, now=NOW)
    grant = verifier.verify(token, artifact_id=replay.artifact_id, now=NOW)

    assert grant.subject == worker.worker_id
    assert grant.source == "gratka"
    assert grant.task_id == lease.task_id
    assert grant.expires_at == lease.lease_expires_at
    with pytest.raises(ArtifactCapabilityError):
        verifier.verify(token, artifact_id=str(uuid4()), now=NOW)
    with pytest.raises(ArtifactCapabilityError):
        verifier.verify(token + "x", artifact_id=replay.artifact_id, now=NOW)
    with pytest.raises(ArtifactCapabilityError):
        verifier.verify(
            token, artifact_id=replay.artifact_id, now=lease.lease_expires_at
        )


def test_capability_key_files_separate_signing_from_verification(tmp_path):
    from homefinder.artifact_capability import (
        load_capability_signer,
        load_capability_verifier,
    )

    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    private_path.chmod(0o600)
    public_path.chmod(0o600)

    assert load_capability_signer(private_path)
    assert load_capability_verifier(public_path)
