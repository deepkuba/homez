"""Dark concurrent workers must stay isolated from database and legacy runtime."""

from pathlib import Path

# Paths below describe per-container tmpfs; this test never writes them.
# ruff: noqa: S108
import pytest
import yaml


@pytest.mark.parametrize("deployment", ["nas", "vps"])
def test_concurrent_workers_are_source_pinned_and_database_isolated(
    deployment: str,
) -> None:
    raw = Path(f"infra/compose.concurrent-scrapers-{deployment}.yaml").read_text()
    compose = yaml.safe_load(raw)
    expected = {
        f"scrape-worker-{deployment}-{source}"
        for source in ("gratka", "morizon", "otodom", "olx")
    }
    services = compose["services"]
    assert set(services) == expected | ({"web"} if deployment == "vps" else set())
    assert len(compose["secrets"]) == 4
    for name in expected:
        worker = services[name]
        source = name.rsplit("-", 1)[1]
        secret = f"scrape_worker_{deployment}_{source}"
        command = worker["command"]
        assert command[:3] == ["python", "-m", "homefinder.scraper.worker"]
        args = dict(zip(command[3::2], command[4::2], strict=True))
        assert args["--source"] == source
        assert args["--deployment"] == deployment
        assert args["--worker-id"] == name
        assert args["--token-file"] == f"/run/secrets/{secret}"
        assert args["--heartbeat-file"] == "/tmp/scrape-heartbeat"
        if deployment == "vps":
            assert args["--coordinator-url"] == "http://web:8000"
            assert set(worker["networks"]) == {
                "concurrent-scraper-control",
                "concurrent-scraper-egress",
            }
        else:
            assert "HOMEZ_SCRAPE_COORDINATOR_URL:?" in args["--coordinator-url"]
            assert worker["networks"] == ["concurrent-scraper-egress"]
        assert worker["secrets"] == [secret]
        assert compose["secrets"][secret]["file"].endswith(f"/{name}-token")
        assert worker["profiles"] == ["concurrent-scrapers"]
        assert worker["image"] == (
            "${HOMEZ_IMAGE:?set image}@"
            "${HOMEZ_SCRAPE_IMAGE_DIGEST:?set immutable digest}"
        )
        assert worker["user"] == "10001:10001"
        assert worker["init"] is True
        assert worker["read_only"] is True
        assert worker["cap_drop"] == ["ALL"]
        assert worker["security_opt"] == ["no-new-privileges:true"]
        assert worker["mem_limit"] == "256m"
        assert worker["cpus"] == 0.5
        assert worker["pids_limit"] == 128
        assert worker["stop_grace_period"] == "30s"
        assert worker["tmpfs"] == ["/tmp:size=16m,mode=1777"]
        assert worker["healthcheck"]["test"] == [
            "CMD",
            "homefinder",
            "runtime-health",
            "--heartbeat-file",
            "/tmp/scrape-heartbeat",
            "--max-age-seconds",
            "90",
        ]
        assert not {"ports", "volumes", "env_file", "environment"} & worker.keys()
    assert "DATABASE" not in raw
    assert "FALLBACK" not in raw
    assert "ENABLED" not in raw
    if deployment == "vps":
        assert compose["networks"]["concurrent-scraper-control"]["internal"] is True
        assert services["web"] == {
            "networks": ["backend", "frontend", "concurrent-scraper-control"]
        }


def test_ci_validates_both_concurrent_compose_models() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text()
    for deployment in ("nas", "vps"):
        assert f"infra/compose.concurrent-scrapers-{deployment}.yaml" in workflow
