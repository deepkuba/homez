import os
from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from homefinder.catalog.orm import FeedbackEventRecord
from homefinder.digest.feedback import FeedbackError, SqlAlchemyFeedbackService

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.mark.postgres
@pytest.mark.skipif(POSTGRES_URL is None, reason="TEST_POSTGRES_URL is not configured")
def test_postgres_feedback_token_has_exactly_one_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    command.upgrade(Config("alembic.ini"), "head")
    sessions = sessionmaker(create_engine(POSTGRES_URL), expire_on_commit=False)
    service = SqlAlchemyFeedbackService(sessions)
    unique = uuid4().hex
    now = datetime.now(timezone.utc)
    token = service.issue(unique, unique, now=now, ttl=timedelta(days=1))
    barrier = Barrier(8)
    results: list[bool] = []

    def consume() -> None:
        barrier.wait()
        try:
            service.record(
                method="POST",
                token=token,
                csrf_token="csrf",  # noqa: S106
                expected_csrf="csrf",
                value="like",
                now=now,
                report_id=unique,
                listing_id=unique,
                actor_hash=unique,
            )
        except FeedbackError:
            results.append(False)
        else:
            results.append(True)

    workers = [Thread(target=consume) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert results.count(True) == 1
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count(FeedbackEventRecord.id)).where(
                    FeedbackEventRecord.report_id == unique
                )
            )
            == 1
        )
