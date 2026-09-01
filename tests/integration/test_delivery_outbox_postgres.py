import os
from datetime import datetime, timezone
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from homefinder.digest.delivery import DeliveryOutbox

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.mark.postgres
@pytest.mark.skipif(POSTGRES_URL is None, reason="TEST_POSTGRES_URL is not configured")
def test_postgres_delivery_claim_is_single_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    command.upgrade(Config("alembic.ini"), "head")
    sessions = sessionmaker(create_engine(POSTGRES_URL), expire_on_commit=False)
    outbox = DeliveryOutbox(sessions)
    unique = uuid4().hex
    period = f"T{unique[:7]}"
    now = datetime.now(timezone.utc)
    outbox.enqueue(
        period=period,
        report_id=str(uuid4()),
        recipient="buyer@example.invalid",
        render_version="test",
        now=now,
    )
    barrier = Barrier(2)
    claims = []

    def claim() -> None:
        barrier.wait()
        claims.append(outbox.claim(now=now))

    workers = [Thread(target=claim) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert sum(claim is not None for claim in claims) == 1
