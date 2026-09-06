from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from homefinder.digest.delivery import DigestDelivery, InMemoryDeliveryLedger
from homefinder.digest.feedback import FeedbackError, FeedbackService, TokenStore
from homefinder.digest.render import Digest, DigestItem, render_digest
from homefinder.domain.matching import (
    MatchExplanation,
    PropertyFacts,
    RuleResult,
    TriState,
)
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
    assert (
        'open listing</a> · <a href="https://homez.invalid/f/secret" '
        'rel="noreferrer">feedback</a>' in html
    )
    assert "https://homez.invalid/f/secret" not in plain
    assert "https://example.invalid/a" in plain


def test_digest_groups_and_escapes_match_criteria() -> None:
    explanation = MatchExplanation(
        (
            RuleResult(
                "area", TriState.PASS, "52 m²", "at least 40 m²", "above by 12 m²"
            ),
            RuleResult(
                "price",
                TriState.FAIL,
                "PLN 820,000",
                "at most PLN 800,000",
                "over by PLN 20,000",
            ),
            RuleResult(
                "<commute>",
                TriState.UNKNOWN,
                "unknown",
                "at most 45 minutes",
                "distance unknown",
            ),
        ),
        (),
        Decimal("8.50"),
        Decimal("0.90"),
        (),
    )
    item = DigestItem(
        RankedCandidate(PropertyFacts(id="one", title="Flat"), explanation),
        "https://example.invalid/a",
    )
    digest = Digest("report-1", datetime(2026, 9, 4, tzinfo=timezone.utc), (), (item,))

    html, plain = render_digest(digest)

    assert "Criteria met" in html
    assert "Criteria not met" in html
    assert "Unknown / needs verification" in html
    assert "area: actual 52 m²; threshold at least 40 m²; above by 12 m²" in html
    assert "price: actual PLN 820,000; threshold at most PLN 800,000" in html
    assert "&lt;commute&gt;" in html
    assert "<commute>" not in html
    assert "Criteria met: area: actual 52 m²" in plain
    assert "Criteria not met: price: actual PLN 820,000" in plain
    assert "Unknown / needs verification: <commute>: actual unknown" in plain


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
