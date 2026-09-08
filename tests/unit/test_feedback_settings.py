from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from homefinder.catalog.orm import Base, BuyerProfileRecord
from homefinder.catalog.profile_repository import SqlAlchemyBuyerProfileRepository
from homefinder.config import Settings
from homefinder.domain.profile import BuyerProfile
from homefinder.web.app import create_app

NOW = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)


def _private(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'settings.sqlite'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = SqlAlchemyBuyerProfileRepository(session)
        repository.add_draft(BuyerProfile(), created_at=NOW)
        repository.approve(1, approved_by="buyer", approved_at=NOW)
    admin = _private(tmp_path / "admin-token", "admin-secret")
    client = TestClient(
        create_app(
            Settings(
                environment="test",
                database_url=database_url,
                admin_bearer_token_file=admin,
                _env_file=None,
            )
        ),
        base_url="https://testserver",
    )
    return client, database_url


def _form(csrf: str) -> dict[str, str]:
    return {
        "csrf_token": csrf,
        "destination": "Rynek Główny 1, Kraków",
        "max_commute_minutes": "35",
        "min_area_sqm": "42.5",
        "min_rooms": "3",
        "max_purchase_price_pln": "850000",
        "core_purchase_price_pln": "780000",
        "max_monthly_installment_pln": "4500",
        "cash_budget_pln": "220000",
        "max_building_dwellings": "60",
        "excluded_localities": "Skawina, Niepołomice",
        "ideal_area_low_sqm": "50",
        "ideal_area_high_sqm": "58",
        "reference_admin_fee_pln": "650",
        "preferred_heating_type": "district",
        "weight_ready_to_move": "20",
        "weight_quiet": "20",
        "weight_green_space": "15",
        "weight_balcony": "15",
        "weight_separate_kitchen": "10",
        "weight_small_building": "10",
        "weight_area_fit": "10",
    }


def test_settings_page_is_private_and_displays_active_criteria(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    assert client.get("/feedback/settings").status_code == 401
    response = client.get("/feedback/settings", auth=("homez", "admin-secret"))

    assert response.status_code == 200
    assert "Ustawienia kryteriów" in response.text
    assert "Aktywna wersja: 1" in response.text
    assert 'name="max_purchase_price_pln" value="800000.00"' in response.text
    assert 'name="reference_admin_fee_pln" value="500.00"' in response.text
    assert 'option value="district" selected' in response.text
    assert "Zakup nieruchomości, nie najem" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_settings_post_validates_csrf_and_profile_invariants(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    page = client.get("/feedback/settings", auth=("homez", "admin-secret"))
    csrf = page.cookies["homefinder_settings_csrf"]

    rejected = client.post(
        "/feedback/settings",
        auth=("homez", "admin-secret"),
        data={**_form("wrong"), "min_rooms": "0"},
    )
    assert rejected.status_code == 400

    client.cookies.set("homefinder_settings_csrf", csrf)
    invalid = client.post(
        "/feedback/settings",
        auth=("homez", "admin-secret"),
        data={**_form(csrf), "ideal_area_low_sqm": "70"},
    )
    assert invalid.status_code == 400
    assert "ideal area" in invalid.json()["detail"]


def test_settings_post_activates_a_new_auditable_profile_version(
    tmp_path: Path,
) -> None:
    client, database_url = _client(tmp_path)
    page = client.get("/feedback/settings", auth=("homez", "admin-secret"))
    csrf = page.cookies["homefinder_settings_csrf"]
    client.cookies.set("homefinder_settings_csrf", csrf)

    response = client.post(
        "/feedback/settings",
        auth=("homez", "admin-secret"),
        data=_form(csrf),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/feedback/settings?saved=1"
    with Session(create_engine(database_url)) as session:
        repository = SqlAlchemyBuyerProfileRepository(session)
        active = repository.active()
        assert active.version == 2
        assert active.destination == "Rynek Główny 1, Kraków"
        assert active.max_purchase_price_minor == 85_000_000
        assert active.reference_admin_fee_including_heating_minor == 65_000
        assert active.preferred_heating_type == "district"
        assert active.excluded_localities == frozenset({"skawina", "niepołomice"})
        assert active.weights()["balcony"] == 15
        assert session.scalar(select(func.count(BuyerProfileRecord.version))) == 2


def test_settings_escape_saved_user_content(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    page = client.get("/feedback/settings", auth=("homez", "admin-secret"))
    csrf = page.cookies["homefinder_settings_csrf"]
    client.cookies.set("homefinder_settings_csrf", csrf)
    payload = _form(csrf)
    payload["destination"] = "<script>alert(1)</script>"

    saved = client.post(
        "/feedback/settings",
        auth=("homez", "admin-secret"),
        data=payload,
        follow_redirects=False,
    )
    assert saved.status_code == 303

    rendered = client.get("/feedback/settings", auth=("homez", "admin-secret"))
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered.text
    assert 'value="<script>alert(1)</script>"' not in rendered.text
