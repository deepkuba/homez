import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from homefinder.artifacts.service import ServiceConfigurationError, build_app, main


def config(tmp_path):
    tmp_path.chmod(0o700)
    root = tmp_path / "objects"
    root.mkdir(mode=0o700)
    key = tmp_path / "key"
    key.write_bytes(base64.b64encode(bytes(range(32))))
    key.chmod(0o600)
    credentials = tmp_path / "credentials"
    credentials.write_text(
        json.dumps(
            {
                "credentials": {
                    "synthetic-token": {
                        "subject": "worker",
                        "role": "worker",
                        "source": "olx",
                        "expires_at": (
                            datetime.now(timezone.utc) + timedelta(minutes=20)
                        ).isoformat(),
                    }
                },
                "frozen_manifests": {},
            }
        )
    )
    credentials.chmod(0o600)
    return dict(
        root=root,
        wrapping_key_file=key,
        credentials_file=credentials,
        audit_file=tmp_path / "audit",
    )


def test_explicit_config_builds_private_app(tmp_path):
    settings = config(tmp_path)
    app = build_app(**settings)
    assert TestClient(app).get("/artifacts").status_code == 404
    assert settings["audit_file"].stat().st_mode & 0o777 == 0o600


def test_static_recovery_credentials_are_rejected(tmp_path):
    from uuid import uuid4

    settings = config(tmp_path)
    value = json.loads(settings["credentials_file"].read_text())
    value["credentials"]["synthetic-token"] = {
        "subject": "recovery-gratka",
        "role": "recovery",
        "source": "gratka",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
        "artifact_ids": [str(uuid4())],
    }
    settings["credentials_file"].write_text(json.dumps(value))

    with pytest.raises(ServiceConfigurationError):
        build_app(**settings)


@pytest.mark.parametrize("name", ["root", "wrapping_key_file", "credentials_file"])
def test_insecure_permissions_fail_closed(tmp_path, name):
    settings = config(tmp_path)
    settings[name].chmod(0o755 if name == "root" else 0o644)
    with pytest.raises(
        ServiceConfigurationError, match="Invalid artifact service configuration"
    ):
        build_app(**settings)


@pytest.mark.parametrize("value", ["not base64 secret", "YQ=="])
def test_invalid_key_is_never_echoed(tmp_path, value):
    settings = config(tmp_path)
    settings["wrapping_key_file"].write_text(value)
    with pytest.raises(ServiceConfigurationError) as error:
        build_app(**settings)
    assert value not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        {"expires_at": "2000-01-01T00:00:00+00:00"},
        {"role": "admin"},
        {"source": "unknown"},
        {"artifact_ids": ["a"]},
        {"subject": "\nraw"},
    ],
)
def test_invalid_scopes_are_rejected(tmp_path, change):
    settings = config(tmp_path)
    value = json.loads(settings["credentials_file"].read_text())
    value["credentials"]["synthetic-token"].update(change)
    settings["credentials_file"].write_text(json.dumps(value))
    with pytest.raises(ServiceConfigurationError):
        build_app(**settings)


def test_symlink_audit_is_rejected(tmp_path):
    settings = config(tmp_path)
    settings["audit_file"].symlink_to(settings["wrapping_key_file"])
    with pytest.raises(ServiceConfigurationError):
        build_app(**settings)


def test_serve_calls_runner_without_network_and_disables_access_logs(
    tmp_path, monkeypatch
):
    settings = config(tmp_path)
    calls = []
    monkeypatch.setattr(
        "homefinder.artifacts.service.uvicorn.run",
        lambda app, **kwargs: calls.append(kwargs),
    )
    args = ["serve"]
    for name, value in settings.items():
        args.extend(["--" + name.replace("_", "-"), str(value)])
    args.extend(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8091",
            "--retention-interval-seconds",
            "3600",
        ]
    )
    assert main(args) == 0
    assert calls[0]["access_log"] is False
    assert calls[0]["port"] == 8091


def test_expire_once_never_starts_network(tmp_path, monkeypatch):
    settings = config(tmp_path)
    monkeypatch.setattr(
        "homefinder.artifacts.service.uvicorn.run",
        lambda *args, **kwargs: pytest.fail("network"),
    )
    args = ["expire-once"]
    for name, value in settings.items():
        args.extend(["--" + name.replace("_", "-"), str(value)])
    assert main(args) == 0


def test_audited_read_persists_only_safe_identifiers(tmp_path):
    from uuid import uuid4

    from homefinder.parsers.contracts import PageInput

    settings = config(tmp_path)
    app = build_app(**settings)
    artifact_id = app.state.artifact_store.store(
        "olx", PageInput(uuid4(), datetime.now(timezone.utc), b"private raw content")
    )
    settings["credentials_file"].write_text(
        json.dumps(
            {
                "credentials": {
                    "synthetic-token": {
                        "subject": "maint",
                        "role": "maintenance",
                        "expires_at": (
                            datetime.now(timezone.utc) + timedelta(minutes=20)
                        ).isoformat(),
                        "artifact_ids": [artifact_id],
                    }
                }
            }
        )
    )
    app = build_app(**settings)
    client = TestClient(app)
    for _ in range(2):
        response = client.get(
            f"/artifacts/{artifact_id}",
            headers={"Authorization": "Bearer synthetic-token"},
        )
        assert response.content == b"private raw content"
    records = settings["audit_file"].read_text()
    assert len(records.splitlines()) == 2
    assert "private raw content" not in records
    assert "synthetic-token" not in records


def test_startup_runs_retention_sweep(tmp_path, monkeypatch):
    app = build_app(**config(tmp_path))
    calls = []
    monkeypatch.setattr(app.state.artifact_store, "expire", lambda: calls.append(True))
    with TestClient(app):
        assert calls == [True]
