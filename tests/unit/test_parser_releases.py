from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from homefinder.catalog.orm import (
    ParserActivationAuditRecord,
    ParserReleaseRecord,
    PortalParserActivationRecord,
)
from homefinder.parser_releases import (
    ActivationRejected,
    ParserReleaseRepository,
    ReleaseBuild,
)

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _build(source="gratka", version="v2", marker="2") -> ReleaseBuild:
    return ReleaseBuild(
        source=source,
        parser_version=f"{source}-{version}",
        git_commit=marker * 40,
        parser_content_hash=marker * 64,
        configuration_hash="3" * 64,
        dependency_lock_hash="4" * 64,
        deployable_digest="sha256:" + "5" * 64,
        qualifying_benchmark_run=f"benchmark-{source}-{version}",
    )


def test_release_hash_changes_with_any_build_input(scrape_queue) -> None:
    _queue, _snapshots, _workers, sessions = scrape_queue
    releases = ParserReleaseRepository(sessions, eligibility=lambda *args: True)

    first = releases.register(_build(), now=NOW)
    same = releases.register(_build(), now=NOW)
    changed = releases.register(_build(version="v3", marker="6"), now=NOW)

    assert first.release_hash == same.release_hash
    assert changed.release_hash != first.release_hash


def test_activation_is_audited_and_rollback_is_portal_isolated(scrape_queue) -> None:
    queue, _snapshots, workers, sessions = scrape_queue
    releases = ParserReleaseRepository(sessions, eligibility=lambda *args: True)
    candidate = releases.register(_build(), now=NOW)
    old_hash = "a" * 64
    for worker in workers:
        queue.register_worker(
            worker,
            release_hashes=(old_hash, candidate.release_hash),
            now=NOW,
        )

    assert (
        releases.activate(
            source="gratka",
            release_hash=candidate.release_hash,
            actor="operator@example.test",
            compared_metrics="reviewed candidate against active",
            expected_epoch=1,
            now=NOW,
        )
        == 2
    )
    assert (
        releases.rollback(
            source="gratka",
            actor="operator@example.test",
            reason="field regression",
            expected_epoch=2,
            now=NOW,
        )
        == 3
    )

    with sessions() as session:
        gratka = session.get(PortalParserActivationRecord, "gratka")
        morizon = session.get(PortalParserActivationRecord, "morizon")
        audits = session.scalars(
            select(ParserActivationAuditRecord).order_by(
                ParserActivationAuditRecord.activation_epoch
            )
        ).all()
        assert (gratka.release_hash, gratka.activation_epoch) == (old_hash, 3)
        assert (morizon.release_hash, morizon.activation_epoch) == ("b" * 64, 1)
        assert [
            (row.action, row.previous_release_hash, row.release_hash) for row in audits
        ] == [
            ("activate", old_hash, candidate.release_hash),
            ("rollback", candidate.release_hash, old_hash),
        ]
        assert (
            session.get(ParserReleaseRecord, candidate.release_hash).status == "revoked"
        )
        assert session.get(ParserReleaseRecord, old_hash).status == "active"


def test_activation_rejects_stale_epoch_without_changing_pointer(scrape_queue) -> None:
    queue, _snapshots, workers, sessions = scrape_queue
    releases = ParserReleaseRepository(sessions, eligibility=lambda *args: True)
    candidate = releases.register(_build(), now=NOW)
    for worker in workers:
        queue.register_worker(
            worker,
            release_hashes=("a" * 64, candidate.release_hash),
            now=NOW,
        )

    with pytest.raises(ActivationRejected, match="activation epoch changed"):
        releases.activate(
            source="gratka",
            release_hash=candidate.release_hash,
            actor="operator@example.test",
            compared_metrics="reviewed candidate against active",
            expected_epoch=9,
            now=NOW,
        )

    with sessions() as session:
        pointer = session.get(PortalParserActivationRecord, "gratka")
        assert (pointer.release_hash, pointer.activation_epoch) == ("a" * 64, 1)
        assert session.scalars(select(ParserActivationAuditRecord)).all() == []
