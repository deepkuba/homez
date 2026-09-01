import os
from threading import Barrier, Thread

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import RoutingQuotaLedgerRecord
from homefinder.routing.quota import QuotaExhausted, QuotaLedger

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.mark.postgres
@pytest.mark.skipif(POSTGRES_URL is None, reason="TEST_POSTGRES_URL is not configured")
def test_postgres_quota_reservation_is_atomic_across_worker_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(POSTGRES_URL)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with Session(engine) as session:
        session.execute(
            delete(RoutingQuotaLedgerRecord).where(
                RoutingQuotaLedgerRecord.period == "2099-01"
            )
        )
        session.commit()

    barrier = Barrier(8)
    outcomes: list[bool] = []

    def reserve() -> None:
        ledger = QuotaLedger(
            sessions,
            period="2099-01",
            provider="concurrency-test",
            billable_unit="route",
            allowance=5,
            safety_ratio=1.0,
        )
        barrier.wait()
        try:
            ledger.reserve()
        except QuotaExhausted:
            outcomes.append(False)
        else:
            outcomes.append(True)

    workers = [Thread(target=reserve) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    ledger = QuotaLedger(
        sessions,
        period="2099-01",
        provider="concurrency-test",
        billable_unit="route",
        allowance=5,
        safety_ratio=1.0,
    )
    assert sum(outcomes) == 5
    assert ledger.snapshot().reserved == 5
