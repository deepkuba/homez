from pathlib import Path

import yaml


def test_compose_defines_least_privilege_runtime_topology() -> None:
    compose_path = Path("infra/compose.yaml")
    raw = compose_path.read_text(encoding="utf-8")
    compose = yaml.safe_load(raw)
    services = compose["services"]
    application_services = {
        "migrate",
        "web",
        "workflow-worker",
        "scheduler",
        "delivery-worker",
    }

    assert application_services | {"db", "ingress", "backup"} <= services.keys()
    assert len({services[name]["image"] for name in application_services}) == 1
    assert "HOMEZ_IMAGE_TAG:?" in services["web"]["image"]
    assert ":-main" not in raw
    assert "${POSTGRES_PASSWORD" not in raw
    assert "HOMEFINDER_DATABASE_URL:" not in raw

    for name in application_services:
        service = services[name]
        assert service["user"] == "10001:10001"
        assert service["read_only"] is True
        assert service["init"] is True
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["cap_drop"] == ["ALL"]
        assert service["pids_limit"] > 0
        assert service["mem_limit"]
        assert service["cpus"]
        assert "ports" not in service

    assert services["migrate"]["restart"] == "no"
    assert services["web"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["ingress"]["ports"] == ["80:80", "443:443"]
    assert "ports" not in services["db"]
    assert compose["networks"]["backend"]["internal"] is True
    assert "internal" not in compose["networks"]["egress"]
    assert set(services["workflow-worker"]["networks"]) == {"backend", "egress"}
    assert set(services["delivery-worker"]["networks"]) == {"backend", "egress"}
    assert "egress" not in services["db"]["networks"]
    assert "egress" not in services["web"]["networks"]
    assert "ports" not in services["workflow-worker"]
    assert "ports" not in services["delivery-worker"]
    assert (
        services["delivery-worker"]["environment"]["HOMEFINDER_FEEDBACK_BASE_URL"]
        == "https://${HOMEZ_DOMAIN:?set HOMEZ_DOMAIN}"
    )
    state_mounts = [
        mount
        for mount in services["workflow-worker"]["volumes"]
        if mount["target"] == "/var/lib/homefinder"
    ]
    assert state_mounts == [
        {
            "type": "bind",
            "source": "${HOMEZ_STATE_DIR:?set HOMEZ_STATE_DIR}",
            "target": "/var/lib/homefinder",
        }
    ]
    assert all(
        mount["target"] != "/var/lib/homefinder/gmail-token.json"
        for mount in services["workflow-worker"]["volumes"]
    )
    assert "backend" not in services["ingress"]["networks"]
    assert set(compose["secrets"]) >= {
        "database_url",
        "postgres_password",
        "gmail_token_key",
        "mail_api_token",
        "feedback_token_key",
        "feedback_rate_salt",
        "admin_bearer_token",
        "report_recipient",
        "backup_key",
    }


def test_ingress_exposes_feedback_only() -> None:
    caddyfile = Path("infra/Caddyfile").read_text(encoding="utf-8")

    assert "handle /feedback/*" in caddyfile
    assert "reverse_proxy web:8000" in caddyfile
    assert "respond 404" in caddyfile
    assert "/corrections" not in caddyfile
    assert "/health" not in caddyfile


def test_shared_vps_override_exposes_only_loopback_web() -> None:
    override_path = Path("infra/compose.shared-vps.yaml")
    raw = override_path.read_text(encoding="utf-8")
    override = yaml.safe_load(raw)
    services = override["services"]

    assert services["ingress"]["profiles"] == ["standalone-ingress"]
    assert services["web"]["ports"] == ["127.0.0.1:18000:8000"]
    assert set(services) == {"ingress", "web"}
    assert all(
        "ports" not in service for name, service in services.items() if name != "web"
    )
    assert "${HOMEZ_TRUSTED_PROXY_IP:?set HOMEZ_TRUSTED_PROXY_IP}" in raw

    frontend = override["networks"]["frontend"]
    ipam = frontend["ipam"]["config"]
    assert ipam == [
        {
            "subnet": "${HOMEZ_FRONTEND_SUBNET:?set HOMEZ_FRONTEND_SUBNET}",
            "gateway": "${HOMEZ_TRUSTED_PROXY_IP:?set HOMEZ_TRUSTED_PROXY_IP}",
        }
    ]


def test_nas_scrapers_are_four_isolated_source_pinned_processes() -> None:
    raw = Path("infra/compose.nas-scrapers.yaml").read_text(encoding="utf-8")
    compose = yaml.safe_load(raw)
    services = compose["services"]
    expected = {"scraper-olx", "scraper-otodom", "scraper-morizon", "scraper-gratka"}

    assert set(services) == expected
    assert set(compose["secrets"]) == {"scraper_token"}
    published = set()
    for name in expected:
        service = services[name]
        source = name.removeprefix("scraper-")
        assert service["read_only"] is True
        assert service["user"] == "10001:10001"
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["secrets"] == ["scraper_token"]
        assert service["command"][0:3] == [
            "homefinder",
            "scraper-server",
            "--source",
        ]
        assert service["command"][3] == source
        assert service["command"][4:8] == [
            "--token-file",
            "/run/secrets/scraper_token",
            "--state-file",
            "/var/lib/homefinder-scraper/rate-limit.json",
        ]
        assert service["volumes"] == [
            {
                "type": "bind",
                "source": "${HOMEZ_SCRAPER_STATE_DIR:?set state dir}/" + source,
                "target": "/var/lib/homefinder-scraper",
            }
        ]
        assert len(service["ports"]) == 1
        assert service["ports"][0].startswith("${HOMEZ_NAS_TAILSCALE_IP:?")
        published.add(service["ports"][0])
    assert len(published) == 4


def test_vps_scraping_override_mounts_token_only_into_workflow_worker() -> None:
    raw = Path("infra/compose.scraping-vps.yaml").read_text(encoding="utf-8")
    compose = yaml.safe_load(raw)

    assert set(compose["services"]) == {"workflow-worker"}
    worker = compose["services"]["workflow-worker"]
    assert worker["secrets"] == ["scraper_token"]
    assert worker["environment"]["HOMEFINDER_SCRAPER_TOKEN_FILE"] == (
        "/run/secrets/scraper_token"
    )
    assert set(compose["secrets"]) == {"scraper_token"}


def test_local_vps_scrapers_are_private_redundant_services() -> None:
    raw = Path("infra/compose.local-scrapers-vps.yaml").read_text(encoding="utf-8")
    compose = yaml.safe_load(raw)
    services = compose["services"]
    scraper_names = {
        "scraper-vps-olx",
        "scraper-vps-otodom",
        "scraper-vps-morizon",
        "scraper-vps-gratka",
    }

    assert set(services) == scraper_names | {"workflow-worker"}
    assert set(compose["secrets"]) == {"scraper_token"}
    assert compose["networks"]["scraper-control"]["internal"] is True
    assert compose["networks"]["scraper-egress"]["driver"] == "bridge"

    worker = services["workflow-worker"]
    assert set(worker["networks"]) == {"backend", "egress", "scraper-control"}
    assert set(worker["depends_on"]) >= scraper_names
    for name in scraper_names:
        source = name.removeprefix("scraper-vps-")
        scraper = services[name]
        assert scraper["read_only"] is True
        assert scraper["user"] == "10001:10001"
        assert scraper["cap_drop"] == ["ALL"]
        assert scraper["security_opt"] == ["no-new-privileges:true"]
        assert scraper["secrets"] == ["scraper_token"]
        assert "ports" not in scraper
        assert set(scraper["networks"]) == {"scraper-control", "scraper-egress"}
        assert scraper["command"][0:4] == [
            "homefinder",
            "scraper-server",
            "--source",
            source,
        ]
        assert scraper["volumes"] == [
            {
                "type": "bind",
                "source": "${HOMEZ_VPS_SCRAPER_STATE_DIR:?set VPS scraper state dir}/"
                + source,
                "target": "/var/lib/homefinder-scraper",
            }
        ]
        endpoint_name = f"HOMEFINDER_SCRAPER_{source.upper()}_FALLBACK_ENDPOINT"
        assert worker["environment"][endpoint_name] == f"http://{name}:8000"


def test_ci_checks_default_shared_model_omits_profiled_ingress() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert 'assert "ingress" not in services' in workflow
    assert 'services["ingress"]["profiles"]' not in workflow


def test_runtime_image_contains_matching_postgresql_client() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "postgresql-client-17=" in dockerfile
    assert "apt.postgresql.org.asc" in dockerfile
