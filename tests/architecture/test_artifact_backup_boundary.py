"""Artifact ciphertext and key material never enter database backup mounts."""

from pathlib import Path

import yaml


def test_artifact_storage_is_opt_in_private_and_nas_local() -> None:
    compose = yaml.safe_load(Path("infra/compose.artifacts-nas.yaml").read_text())
    assert set(compose["services"]) == {"artifacts"}
    service = compose["services"]["artifacts"]
    assert service["profiles"] == ["artifacts"]
    assert service["ports"] == [
        "${HOMEZ_NAS_TAILSCALE_IP:?set NAS Tailscale IP}:18105:8000"
    ]
    assert service["read_only"] is True
    assert service["user"] == "10001:10001"
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["mem_limit"] == "256m"
    assert service["cpus"] == 0.5
    assert service["pids_limit"] == 128
    assert service["command"][:3] == ["python", "-m", "homefinder.artifacts.service"]
    assert service["command"][3] == "serve"
    args = dict(zip(service["command"][4::2], service["command"][5::2], strict=True))
    assert args["--retention-interval-seconds"] == "3600"
    assert args["--wrapping-key-file"] == "/run/secrets/artifact_kek"
    assert args["--credentials-file"] == "/run/secrets/artifact_credentials"
    assert "environment" not in service
    assert set(service["secrets"]) == {"artifact_kek", "artifact_credentials"}
    assert compose["secrets"]["artifact_kek"]["file"] == (
        "${HOMEZ_ARTIFACT_SECRETS_DIR:?set NAS artifact secrets dir}/artifact-kek"
    )
    mounts = service["volumes"]
    assert {mount["source"] for mount in mounts} == {
        "${HOMEZ_ARTIFACT_DATA_DIR:?set NAS artifact data dir}",
        "${HOMEZ_ARTIFACT_AUDIT_DIR:?set NAS artifact audit dir}",
    }
    assert all(mount["type"] == "bind" for mount in mounts)
    assert all(mount["bind"]["create_host_path"] is False for mount in mounts)


def test_all_backup_services_exclude_artifact_mounts_and_secrets() -> None:
    artifact = yaml.safe_load(Path("infra/compose.artifacts-nas.yaml").read_text())
    service = artifact["services"]["artifacts"]
    protected_sources = {mount["source"] for mount in service["volumes"]}
    protected_sources.update(value["file"] for value in artifact["secrets"].values())
    found_backup = False
    for path in Path("infra").glob("compose*.yaml"):
        compose = yaml.safe_load(path.read_text())
        for name, candidate in compose.get("services", {}).items():
            if "backup" not in name:
                continue
            found_backup = True
            for mount in candidate.get("volumes", []):
                source = (
                    mount["source"] if isinstance(mount, dict) else mount.split(":")[0]
                )
                assert source not in protected_sources, (path, name, source)
                assert "ARTIFACT" not in source.upper(), (path, name, source)
            for secret in candidate.get("secrets", []):
                source = secret["source"] if isinstance(secret, dict) else secret
                assert "artifact" not in source.lower(), (path, name, source)
                assert compose["secrets"][source]["file"] not in protected_sources
    assert found_backup
