import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from homefinder.parsers.contracts import PageInput

NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)


def test_artifact_client_streams_exact_bytes_with_source_scope(tmp_path: Path) -> None:
    from homefinder.artifacts.client import HttpArtifactWriter

    token = tmp_path / "artifact-token"
    token.write_text("synthetic-artifact-token")
    token.chmod(0o600)
    artifact_id = str(uuid4())
    calls = []

    def request(path, headers, body, bearer):
        calls.append((path, headers, body, bearer))
        return 201, json.dumps({"artifact_id": artifact_id}).encode()

    page = PageInput(uuid4(), NOW, b"synthetic exact parser bytes\x00\xff")
    result = HttpArtifactWriter(
        "http://100.64.0.10:18105", token, request=request
    ).store("gratka", page)

    assert result == artifact_id
    assert calls[0][0] == "/artifacts/gratka"
    assert calls[0][2] == page.body
    assert calls[0][1]["X-Capture-Id"] == str(page.capture_id)
    assert "synthetic-artifact-token" not in repr(calls[0][1])


def test_artifact_reader_is_exact_id_bounded_and_credential_scoped(
    tmp_path: Path,
) -> None:
    from homefinder.artifacts.client import HttpArtifactReader

    token = tmp_path / "recovery-token"
    token.write_text("synthetic-recovery-token")
    token.chmod(0o600)
    artifact_id = str(uuid4())
    calls = []

    def request(path, bearer):
        calls.append((path, bearer))
        return 200, b"synthetic retained bytes"

    body = HttpArtifactReader("http://artifacts:18105", request=request).read(
        artifact_id, "synthetic-recovery-token"
    )

    assert body == b"synthetic retained bytes"
    assert calls == [(f"/artifacts/{artifact_id}", "synthetic-recovery-token")]
