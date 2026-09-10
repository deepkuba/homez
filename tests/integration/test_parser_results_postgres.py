import hashlib
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)
def test_partial_page_result_keeps_field_provenance(scrape_queue):
    from homefinder.catalog.orm import (
        DiagnosticRunRecord,
        ProductionFieldCandidateRecord,
        ProductionResolvedFieldRecord,
    )
    from homefinder.parsers.contracts import (
        FieldCandidate,
        FieldState,
        PageFacts,
        ParserResult,
        resolve_fields,
    )
    from homefinder.scrape_queue.contracts import CaptureOutcome

    queue, snapshots, workers, sessions = scrape_queue
    queue.enqueue(source="gratka", snapshot_id=snapshots[0], now=NOW)
    lease = queue.claim(workers[0], now=NOW)
    capture_id = uuid4()
    candidates = (
        FieldCandidate("price", 52_000_000, "email", "alert", lease.release_hash),
        FieldCandidate("price", 50_000_000, "summary", "price", lease.release_hash),
        FieldCandidate("rooms", 2, "attributes", "rooms-a", lease.release_hash),
        FieldCandidate("rooms", 3, "attributes", "rooms-b", lease.release_hash),
    )
    fields = resolve_fields(candidates)
    result = ParserResult(
        capture_id,
        lease.release_hash,
        "synthetic",
        candidates,
        tuple(field.name for field in fields if field.state is not FieldState.VALUE),
        facts=PageFacts(title="Synthetic partial", price_minor=50_000_000),
        fields=fields,
    )
    queue.complete(
        workers[0],
        lease,
        CaptureOutcome(
            capture_id,
            NOW,
            hashlib.sha256(b"synthetic").hexdigest(),
            9,
            result,
            str(uuid4()),
        ),
        now=NOW,
    )

    restored = queue.outcome(source="gratka", snapshot_id=snapshots[0], now=NOW)
    assert restored is not None
    by_name = {field.name: field for field in restored.fields}
    assert by_name["price"].state is FieldState.VALUE
    assert by_name["price"].value == 50_000_000
    assert by_name["price"].selected_origin == "summary"
    assert by_name["rooms"].state is FieldState.AMBIGUOUS
    assert by_name["area"].state is FieldState.UNKNOWN
    with sessions() as session:
        assert session.query(ProductionFieldCandidateRecord).count() == 4
        assert session.query(ProductionResolvedFieldRecord).count() == 12
        assert session.query(DiagnosticRunRecord).count() == 1
