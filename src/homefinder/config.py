from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from homefinder.sources.gmail import read_secret_text


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Validated runtime configuration loaded from HOMEFINDER_* variables."""

    model_config = SettingsConfigDict(
        env_prefix="HOMEFINDER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    environment: Environment = Environment.DEVELOPMENT
    database_url: SecretStr = SecretStr("sqlite:///homefinder-preview.db")
    database_url_file: Path | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    gmail_token_file: Path | None = None
    gmail_token_key_file: Path | None = None
    gmail_source_policy_file: Path | None = None
    gmail_mailbox_key: str = "primary"
    report_recipient_file: Path | None = None
    mail_api_token_file: Path | None = None
    mail_api_endpoint: str | None = None
    mail_api_host: str | None = None
    mail_sender: str | None = None
    feedback_base_url: str | None = None
    feedback_token_key_file: Path | None = None
    feedback_rate_salt_file: Path | None = None
    admin_bearer_token_file: Path | None = None
    backup_key_file: Path | None = None

    @model_validator(mode="after")
    def validate_database(self) -> Settings:
        if self.database_url_file is not None:
            object.__setattr__(
                self,
                "database_url",
                SecretStr(read_secret_text(self.database_url_file)),
            )
        raw_url = self.database_url.get_secret_value()
        try:
            parsed_url = make_url(raw_url)
        except ArgumentError as error:
            raise ValueError(
                "database_url must be a valid SQLAlchemy database URL"
            ) from error
        if self.environment is Environment.PRODUCTION and not (
            parsed_url.drivername == "postgresql"
            or parsed_url.drivername.startswith("postgresql+")
        ):
            raise ValueError("production requires a PostgreSQL database URL")
        return self
