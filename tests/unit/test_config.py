import pytest
from pydantic import ValidationError

from homefinder.config import Environment, Settings


def test_development_configuration_has_safe_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.database_url.get_secret_value().startswith("sqlite:///")


def test_production_requires_postgresql() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL database URL"):
        Settings(environment="production", _env_file=None)


def test_production_database_secret_is_redacted() -> None:
    password = "not-a-real-password"
    settings = Settings(
        environment="production",
        database_url=f"postgresql+psycopg://homefinder:{password}@db/homefinder",
        _env_file=None,
    )

    assert settings.environment is Environment.PRODUCTION
    assert password not in repr(settings)
    assert password not in str(settings)


def test_invalid_database_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="valid SQLAlchemy database URL"):
        Settings(database_url="not a database URL", _env_file=None)


def test_settings_load_homefinder_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMEFINDER_ENVIRONMENT", "test")
    monkeypatch.setenv("HOMEFINDER_DATABASE_URL", "sqlite:///test.sqlite3")

    settings = Settings(_env_file=None)

    assert settings.environment is Environment.TEST
    assert settings.database_url.get_secret_value() == "sqlite:///test.sqlite3"


def test_validation_errors_do_not_expose_database_password() -> None:
    password = "short-secret"

    with pytest.raises(ValidationError) as captured:
        Settings(
            environment="production",
            database_url=f"mysql://user:{password}@db/homefinder",
            _env_file=None,
        )

    assert password not in str(captured.value)


def test_log_level_is_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(log_level="TRACE", _env_file=None)
