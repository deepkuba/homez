from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def test_raw_read_requires_exact_artifact_scope_and_purpose() -> None:
    from homefinder.parser_maintenance import (
        MaintenanceAuthorizationError,
        RawReadGrant,
    )

    grant = RawReadGrant(
        subject="reviewer@example.test",
        artifact_id="artifact-a",
        purpose="parser-difference-review",
        expires_at=NOW + timedelta(minutes=30),
    )

    grant.authorize(
        artifact_id="artifact-a", purpose="parser-difference-review", now=NOW
    )
    with pytest.raises(MaintenanceAuthorizationError):
        grant.authorize(
            artifact_id="artifact-b", purpose="parser-difference-review", now=NOW
        )
    with pytest.raises(MaintenanceAuthorizationError):
        grant.authorize(artifact_id="artifact-a", purpose="bulk-export", now=NOW)


def test_maintenance_api_has_bounded_metadata_and_one_raw_stream() -> None:
    from homefinder.parser_maintenance import ScopedTokenIssuer, create_maintenance_app

    class Catalog:
        def metadata(self, *, cursor, limit):
            return ([{"artifact_id": "artifact-a", "safe": True}], None)

        def clusters(self, *, cursor, limit):
            return ([{"signature": "abc", "count": 2}], None)

    issuer = ScopedTokenIssuer(b"k" * 32)
    token = issuer.issue(
        forced_command="homez-artifacts issue-token",
        subject="reviewer@example.test",
        artifact_id="artifact-a",
        purpose="parser-difference-review",
        now=NOW,
    )
    audits = []
    app = create_maintenance_app(
        Catalog(),
        issuer=issuer,
        read_artifact=lambda artifact_id: b"<script>synthetic</script>",
        audit=lambda *event: audits.append(event),
        clock=lambda: NOW,
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    assert (
        client.get("/metadata", headers=headers, params={"limit": 101}).status_code
        == 422
    )
    raw = client.get(
        "/raw/artifact-a",
        headers=headers,
        params={"purpose": "parser-difference-review"},
    )
    assert raw.status_code == 200
    assert raw.text == "<script>synthetic</script>"
    assert raw.headers["cache-control"] == "no-store"
    assert audits == [
        ("reviewer@example.test", "artifact-a", "parser-difference-review")
    ]
    assert client.get("/raw", headers=headers).status_code == 404
    assert client.get("/raw/*", headers=headers).status_code in {403, 404, 422}


def test_forced_command_token_expires_after_thirty_minutes() -> None:
    from homefinder.parser_maintenance import (
        MaintenanceAuthorizationError,
        ScopedTokenIssuer,
    )

    issuer = ScopedTokenIssuer(b"k" * 32)
    with pytest.raises(MaintenanceAuthorizationError):
        issuer.issue(
            forced_command="bash",
            subject="reviewer@example.test",
            artifact_id="artifact-a",
            purpose="parser-difference-review",
            now=NOW,
        )
    token = issuer.issue(
        forced_command="homez-artifacts issue-token",
        subject="reviewer@example.test",
        artifact_id="artifact-a",
        purpose="parser-difference-review",
        now=NOW,
    )
    grant = issuer.verify(token)
    with pytest.raises(MaintenanceAuthorizationError):
        grant.authorize(
            artifact_id="artifact-a",
            purpose="parser-difference-review",
            now=NOW + timedelta(minutes=30),
        )


def test_thin_cli_exposes_only_metadata_and_exact_raw_stream(
    tmp_path, monkeypatch
) -> None:
    from homefinder import artifact_cli

    token_file = tmp_path / "token"
    token_file.write_text("synthetic-token", encoding="utf-8")
    requests = []

    class Reply(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def fake_open(request, timeout):
        requests.append((request, timeout))
        return Reply(b"safe metadata")

    monkeypatch.setattr(artifact_cli, "urlopen", fake_open)
    assert (
        artifact_cli.main(
            [
                "--base-url",
                "https://maintenance.example.test",
                "--token-file",
                str(token_file),
                "metadata",
                "--limit",
                "10",
            ]
        )
        == 0
    )
    assert requests[0][0].headers["Cache-control"] == "no-store"
    with pytest.raises(SystemExit):
        artifact_cli.main(
            [
                "--base-url",
                "https://maintenance.example.test",
                "--token-file",
                str(token_file),
                "bulk-download",
            ]
        )
