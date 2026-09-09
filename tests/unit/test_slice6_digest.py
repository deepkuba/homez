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

    assert "Oferty spełniające kryteria" in html
    assert "Oferty do rozważenia" in html
    assert "&lt;b&gt;Bright flat&lt;/b&gt;" in html
    assert (
        '<a href="https://homez.invalid/f/secret" rel="noreferrer" '
        'style="color:#334155;margin-left:16px">Oceń ofertę</a>' in html
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

    assert "Spełnione" in html
    assert "Mocno przekroczone" in html
    assert "Brak danych" in html
    assert "Powierzchnia" in html
    assert "above by 12 m²" in html
    assert "PLN 820,000" in html
    assert "at most PLN 800,000" in html
    assert "&lt;commute&gt;" in html
    assert "<commute>" not in html
    assert "[Spełnione] Powierzchnia: 52 m²" in plain
    assert "[Mocno przekroczone] Cena: PLN 820,000" in plain
    assert "[Brak danych] <commute>: Brak danych" in plain


def test_digest_renders_non_blocking_preferences_as_criteria() -> None:
    explanation = MatchExplanation(
        (),
        (),
        Decimal("8.50"),
        Decimal("0.90"),
        (),
        (
            RuleResult(
                "heating",
                TriState.PASS,
                "district",
                "district heating / MPEC",
                "meets preference",
            ),
        ),
    )
    item = DigestItem(
        RankedCandidate(PropertyFacts(id="one", title="Flat"), explanation),
        "https://example.invalid/a",
    )

    html, plain = render_digest(
        Digest("report-1", datetime(2026, 9, 4, tzinfo=timezone.utc), (item,), ())
    )

    assert "Ogrzewanie" in html
    assert "district" in html
    assert "[Spełnione] Ogrzewanie: district" in plain


def test_digest_uses_separated_cards_and_color_coded_criteria_table() -> None:
    explanation = MatchExplanation(
        (
            RuleResult("area_sqm", TriState.PASS, "52 m²", "at least 40 m²", "met"),
            RuleResult(
                "price",
                TriState.FAIL,
                "PLN 820,000",
                "at most PLN 800,000",
                "over by PLN 20,000",
                deviation_ratio=Decimal("0.025"),
            ),
            RuleResult(
                "monthly_admin_fee",
                TriState.UNKNOWN,
                "unknown",
                "at most PLN 1,000",
                "distance_unknown",
            ),
            RuleResult(
                "commute_minutes",
                TriState.FAIL,
                "70 minutes",
                "at most 45 minutes",
                "over by 25 minutes",
                deviation_ratio=Decimal("0.56"),
            ),
        ),
        (),
        Decimal("55.00"),
        Decimal("0.90"),
        (),
    )
    items = tuple(
        DigestItem(
            RankedCandidate(
                PropertyFacts(id=identifier, title=f"Flat {identifier}"), explanation
            ),
            f"https://example.invalid/{identifier}",
        )
        for identifier in ("one", "two")
    )

    html, plain = render_digest(
        Digest("report-1", datetime(2026, 9, 4, tzinfo=timezone.utc), items, ())
    )

    assert html.count('class="listing-card"') == 2
    assert "border:1px solid" in html
    assert "margin-bottom:24px" in html
    assert '<table role="presentation"' in html
    assert ">Kryterium</th>" in html
    assert 'data-status="met"' in html
    assert 'data-status="slight"' in html
    assert 'data-status="unknown"' in html
    assert 'data-status="strong"' in html
    assert "Dopasowanie: 55/100" in html
    assert "Powierzchnia" in html
    assert "Czynsz administracyjny" in html
    assert "distance unknown" in html
    assert "monthly_admin_fee" not in html
    assert "monthly_admin_fee" not in plain
    assert "Skontaktuj się" not in html
    assert "Skontaktuj sie" not in html


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


def test_delivery_ledger_is_idempotent_and_daily_schedule_is_warsaw() -> None:
    ledger = InMemoryDeliveryLedger()
    delivery = DigestDelivery(ledger)
    scheduled = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)  # 17:00 Warsaw
    assert delivery.is_due(scheduled)
    assert delivery.send_once("2026-09-04", lambda: None) is True
    assert (
        delivery.send_once(
            "2026-09-04", lambda: (_ for _ in ()).throw(AssertionError())
        )
        is False
    )
