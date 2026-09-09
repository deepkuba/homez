from datetime import datetime, timezone
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_concurrent_features_default_off() -> None:
    from homefinder.config import Settings

    settings = Settings(_env_file=None)
    assert settings.concurrent_scraping_enabled is False
    assert settings.candidate_benchmark_enabled is False


def test_parser_input_is_bounded_and_not_logged() -> None:
    from homefinder.parsers.contracts import PageInput

    page = PageInput(uuid4(), datetime.now(timezone.utc), b"synthetic")
    assert "synthetic" not in repr(page)
    with pytest.raises(ValueError, match="2 MB"):
        PageInput(uuid4(), page.fetched_at, b"x" * 2_000_001)


def test_task_classes_exclude_benchmark() -> None:
    from homefinder.scrape_queue.contracts import TaskClass

    assert {item.value for item in TaskClass} == {
        "live",
        "artifact_recovery",
        "network_recovery",
    }


def test_expand_migration_keeps_catalog_and_capture_distinct(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'expand.sqlite3'}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "20260908_21")
    engine = create_engine(url)
    before = set(inspect(engine).get_table_names())
    command.upgrade(Config("alembic.ini"), "20260909_22")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) - before == {
        "page_captures",
        "scrape_tasks",
        "parser_releases",
    }
    columns = {c["name"] for c in inspector.get_columns("page_captures")}
    assert {"snapshot_id", "fetched_at", "content_hash", "size_bytes"} <= columns
    assert not columns & {"body", "raw_bytes", "html"}
    assert inspector.get_foreign_keys("page_captures")[0]["referred_table"] == (
        "listing_snapshots"
    )
    engine.dispose()
