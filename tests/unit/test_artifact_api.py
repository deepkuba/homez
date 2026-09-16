from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from homefinder.artifacts.api import ArtifactIdentity, create_artifact_app
from homefinder.parsers.contracts import MAX_PAGE_BYTES

NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


class Store:
    def __init__(self):
        self.uploads = []
        self.reads = []

    def store(self, source, page):
        self.uploads.append((source, page))
        return "artifact-a"

    def read(self, artifact_id):
        self.reads.append(artifact_id)
        if artifact_id == "missing":
            raise FileNotFoundError("secret raw content")
        return b"synthetic raw bytes"


def setup_api():
    store, audit = Store(), []
    credentials = {
        "worker": ArtifactIdentity(
            "worker-1", "worker", NOW + timedelta(minutes=30), source="olx"
        ),
        "maintenance": ArtifactIdentity(
            "maint-1",
            "maintenance",
            NOW + timedelta(minutes=30),
            artifact_ids=frozenset({"artifact-a", "missing"}),
        ),
        "benchmark": ArtifactIdentity(
            "bench-1",
            "benchmark",
            NOW + timedelta(minutes=30),
            artifact_ids=frozenset({"artifact-a"}),
            benchmark_id="run-1",
        ),
        "recovery": ArtifactIdentity(
            "recovery-1",
            "recovery",
            NOW + timedelta(minutes=5),
            source="olx",
            artifact_ids=frozenset({"artifact-a"}),
        ),
        "expired": ArtifactIdentity(
            "old", "maintenance", NOW, artifact_ids=frozenset({"artifact-a"})
        ),
    }
    app = create_artifact_app(
        store,
        credentials=credentials,
        frozen_manifests={"run-1": frozenset({"artifact-a"})},
        audit=audit.append,
        clock=lambda: NOW,
    )
    return TestClient(app), store, audit


def headers(token="worker"):  # noqa: S107 - synthetic test credential
    return {
        "Authorization": f"Bearer {token}",
        "X-Capture-Id": str(uuid4()),
        "X-Fetched-At": NOW.isoformat(),
    }


def test_worker_upload_is_source_pinned_and_read_forbidden():
    client, store, _ = setup_api()
    response = client.post("/artifacts/olx", content=b"exact bytes", headers=headers())
    assert response.status_code == 201
    assert response.json() == {"artifact_id": "artifact-a"}
    assert store.uploads[0][1].body == b"exact bytes"
    assert (
        client.post("/artifacts/gratka", content=b"x", headers=headers()).status_code
        == 403
    )
    assert client.get("/artifacts/artifact-a", headers=headers()).status_code == 403


@pytest.mark.parametrize("token", [None, "unknown", "expired"])
def test_invalid_credentials_are_rejected_without_store_access(token):
    client, store, _ = setup_api()
    response = client.get(
        "/artifacts/artifact-a", headers={} if token is None else headers(token)
    )
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert store.reads == []


@pytest.mark.parametrize("token", ["maintenance", "benchmark"])
def test_exact_read_scope_audit_and_no_bulk_operations(token):
    client, store, audit = setup_api()
    response = client.get("/artifacts/artifact-a", headers=headers(token))
    assert response.content == b"synthetic raw bytes"
    assert response.headers["cache-control"] == "no-store"
    assert len(audit) == 1
    assert audit[0].artifact_id == "artifact-a"
    assert "synthetic raw" not in repr(audit)
    assert client.get("/artifacts/other", headers=headers(token)).status_code == 403
    assert client.get("/artifacts", headers=headers(token)).status_code == 404
    assert (
        client.post("/artifacts/olx", content=b"x", headers=headers(token)).status_code
        == 403
    )
    assert store.reads == ["artifact-a"]


@pytest.mark.parametrize("content_length", [None, "1", str(MAX_PAGE_BYTES + 1)])
def test_upload_enforces_actual_stream_limit(content_length):
    client, store, _ = setup_api()
    request_headers = headers()
    if content_length is not None:
        request_headers["Content-Length"] = content_length
    response = client.post(
        "/artifacts/olx",
        content=iter([b"a" * 1_000_000, b"b" * 1_000_001]),
        headers=request_headers,
    )
    assert response.status_code == 413
    assert store.uploads == []


def test_failed_read_is_audited_and_errors_hide_store_details():
    client, _, audit = setup_api()
    response = client.get("/artifacts/missing", headers=headers("maintenance"))
    assert response.status_code == 404
    assert "secret" not in response.text
    assert len(audit) == 1


def test_audit_failure_prevents_raw_read():
    store = Store()

    def fail(event):
        raise RuntimeError("secret audit details")

    identity = ArtifactIdentity(
        "maint",
        "maintenance",
        NOW + timedelta(minutes=1),
        artifact_ids=frozenset({"artifact-a"}),
    )
    app = create_artifact_app(
        store, credentials={"token": identity}, audit=fail, clock=lambda: NOW
    )
    response = TestClient(app).get("/artifacts/artifact-a", headers=headers("token"))
    assert response.status_code == 503
    assert store.reads == []
    assert "secret" not in response.text


def test_benchmark_manifest_is_frozen_at_app_creation():
    store = Store()
    manifests = {"run": {"artifact-a"}}
    identity = ArtifactIdentity(
        "bench",
        "benchmark",
        NOW + timedelta(minutes=1),
        artifact_ids=frozenset({"artifact-a"}),
        benchmark_id="run",
    )
    app = create_artifact_app(
        store,
        credentials={"token": identity},
        frozen_manifests=manifests,
        audit=lambda event: None,
        clock=lambda: NOW,
    )
    manifests["run"].add("other")
    response = TestClient(app).get("/artifacts/other", headers=headers("token"))
    assert response.status_code == 403
    assert store.reads == []


def test_benchmark_read_requires_exact_identity_and_manifest_scope():
    store = Store()
    identity = ArtifactIdentity(
        "bench",
        "benchmark",
        NOW + timedelta(minutes=1),
        artifact_ids=frozenset({"artifact-a"}),
        benchmark_id="run",
    )
    app = create_artifact_app(
        store,
        credentials={"token": identity},
        frozen_manifests={"run": frozenset({"artifact-a", "artifact-b"})},
        audit=lambda event: None,
        clock=lambda: NOW,
    )

    response = TestClient(app).get("/artifacts/artifact-b", headers=headers("token"))

    assert response.status_code == 403
    assert store.reads == []


def test_recovery_read_requires_exact_short_lived_source_scoped_identity():
    client, store, audit = setup_api()

    response = client.get("/artifacts/artifact-a", headers=headers("recovery"))

    assert response.status_code == 200
    assert response.content == b"synthetic raw bytes"
    assert audit[0].subject == "recovery-1"
    assert (
        client.get("/artifacts/other", headers=headers("recovery")).status_code == 403
    )
    assert store.reads == ["artifact-a"]


def test_recovery_read_rejects_credential_valid_beyond_thirty_minutes():
    store = Store()
    identity = ArtifactIdentity(
        "recovery-long-lived",
        "recovery",
        NOW + timedelta(minutes=31),
        source="olx",
        artifact_ids=frozenset({"artifact-a"}),
    )
    app = create_artifact_app(
        store,
        credentials={"recovery-token": identity},
        audit=lambda event: None,
        clock=lambda: NOW,
    )

    response = TestClient(app).get(
        "/artifacts/artifact-a", headers=headers("recovery-token")
    )

    assert response.status_code == 401
    assert store.reads == []


@pytest.mark.parametrize(
    "header,value",
    [
        ("X-Capture-Id", "private invalid value"),
        ("X-Fetched-At", "private invalid value"),
        ("X-Fetched-At", "2026-09-10T00:00:00"),
        ("Content-Length", "private invalid value"),
    ],
)
def test_upload_metadata_errors_do_not_echo_input(header, value):
    client, store, _ = setup_api()
    request_headers = headers()
    request_headers[header] = value
    response = client.post("/artifacts/olx", content=b"raw", headers=request_headers)
    assert response.status_code == 400
    assert value not in response.text
    assert store.uploads == []
