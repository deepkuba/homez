from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from homefinder.digest.delivery import DigestDelivery, InMemoryDeliveryLedger
from homefinder.digest.feedback import FeedbackError, FeedbackService, TokenStore
from homefinder.digest.render import Digest, DigestItem, render_digest
from homefinder.domain.matching import MatchExplanation, PropertyFacts
from homefinder.domain.ranking import RankedCandidate


def _item(identifier: str = "one") -> DigestItem:
    facts = PropertyFacts(id=identifier, title="<b>Bright flat</b>", locality="Kraków")
    explanation = MatchExplanation((), (), Decimal("8.50"), Decimal("0.90"), ())
    return DigestItem(RankedCandidate(facts, explanation), "https://example.invalid/a")


def test_digest_has_10_plus_10_sections_and_plain_text() -> None:
    digest = Digest(
        "report-1", datetime(2026, 9, 4, tzinfo=timezone.utc), (_item(),), (_item("x"),)
    )
    html, plain = render_digest(
        digest, token_urls={"one": "https://homez.invalid/f/secret"}
    )

    assert "Compliant homes" in html
    assert "Exploration homes" in html
    assert "&lt;b&gt;Bright flat&lt;/b&gt;" in html
    assert "https://homez.invalid/f/secret" not in plain
    assert "https://example.invalid/a" in plain


def test_feedback_requires_post_csrf_and_single_use_scoped_token() -> None:
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    tokens = TokenStore()
    token = tokens.issue("report-1", "one", now=now, ttl=timedelta(days=7))
    service = FeedbackService(tokens)

    with pytest.raises(FeedbackError, match="POST"):
        service.record(
            method="GET",
            token=token,
            csrf_token="csrf",  # noqa: S106
            expected_csrf="csrf",
            value="like",
            now=now,
        )
    event = service.record(
        method="POST",
        token=token,
        csrf_token="csrf",  # noqa: S106
        expected_csrf="csrf",
        value="like",
        now=now,
    )
    assert event.listing_id == "one"
    with pytest.raises(FeedbackError, match="used"):
        service.record(
            method="POST",
            token=token,
            csrf_token="csrf",  # noqa: S106
            expected_csrf="csrf",
            value="like",
            now=now,
        )


def test_delivery_ledger_is_idempotent_and_friday_schedule_is_warsaw() -> None:
    ledger = InMemoryDeliveryLedger()
    delivery = DigestDelivery(ledger)
    friday = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)  # 10:00 Warsaw
    assert delivery.is_due(friday)
    assert delivery.send_once("2026-W36", lambda: None) is True
    assert (
        delivery.send_once("2026-W36", lambda: (_ for _ in ()).throw(AssertionError()))
        is False
    )
