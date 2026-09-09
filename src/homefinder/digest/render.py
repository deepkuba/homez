"""Escaped email and share-safe digest representations."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html import escape

from homefinder.domain.matching import MatchExplanation, RuleResult, TriState
from homefinder.domain.ranking import RankedCandidate
from homefinder.enrichment.primary_market import PrimaryMarketDossier


@dataclass(frozen=True, slots=True)
class DigestItem:
    candidate: RankedCandidate
    listing_url: str
    primary_market: PrimaryMarketDossier | None = None


@dataclass(frozen=True, slots=True)
class Digest:
    report_id: str
    generated_at: datetime
    compliant: tuple[DigestItem, ...]
    exploration: tuple[DigestItem, ...]


@dataclass(frozen=True, slots=True)
class _PresentationStatus:
    key: str
    label: str
    foreground: str
    background: str


_MET = _PresentationStatus("met", "Spełnione", "#166534", "#dcfce7")
_SLIGHT = _PresentationStatus(
    "slight", "Nieznacznie przekroczone", "#854d0e", "#fef9c3"
)
_UNKNOWN = _PresentationStatus("unknown", "Brak danych", "#475569", "#f1f5f9")
_STRONG = _PresentationStatus("strong", "Mocno przekroczone", "#991b1b", "#fee2e2")
_SLIGHT_DEVIATION_LIMIT = Decimal("0.10")

_RULE_LABELS = {
    "transaction": "Typ transakcji",
    "primary_market_evidence": "Rynek pierwotny",
    "vacant_possession": "Wolne przy zakupie",
    "separate_ownership": "Odrębna własność",
    "legal_risk": "Ryzyko prawne",
    "locality": "Lokalizacja",
    "price": "Cena",
    "cash": "Wymagana gotówka",
    "installment": "Miesięczna rata",
    "area": "Powierzchnia",
    "area_sqm": "Powierzchnia",
    "rooms": "Liczba pokoi",
    "usable_layout": "Funkcjonalny układ",
    "floor": "Piętro",
    "elevator": "Winda",
    "parking": "Parking",
    "building_scale": "Wielkość budynku",
    "commute": "Dojazd",
    "commute_minutes": "Dojazd",
    "monthly_admin_fee": "Czynsz administracyjny",
    "heating": "Ogrzewanie",
}


def render_digest(
    digest: Digest, *, token_urls: dict[str, str] | None = None
) -> tuple[str, str]:
    token_urls = token_urls or {}
    sections: list[str] = []
    plain_sections: list[str] = []
    for heading, section_key, items in (
        ("Oferty spełniające kryteria", "compliant", digest.compliant),
        ("Oferty do rozważenia", "exploration", digest.exploration),
    ):
        cards: list[str] = []
        lines = [heading]
        sorted_items = sorted(
            items,
            key=lambda item: (
                -item.candidate.explanation.score,
                item.candidate.facts.id,
            ),
        )
        for position, item in enumerate(sorted_items, start=1):
            card, plain_card = _render_item(
                item,
                section_key=section_key,
                position=position,
                feedback_url=token_urls.get(item.candidate.facts.id),
            )
            cards.append(card)
            lines.append(plain_card)
        empty = '<p style="color:#64748b">Brak ofert.</p>'
        sections.append(
            f'<section><h2 style="color:#0f172a;font-size:20px">{heading}</h2>'
            f"{''.join(cards) or empty}</section>"
        )
        plain_sections.extend(lines)
    html = (
        '<!doctype html><meta name="referrer" content="no-referrer">'
        '<main style="background:#f8fafc;color:#0f172a;font-family:Arial,sans-serif;'
        'margin:0 auto;max-width:760px;padding:24px">'
        '<h1 style="font-size:26px;margin:0 0 24px">Homez — dzienny raport ofert</h1>'
        f"{''.join(sections)}</main>"
    )
    return html, "Homez — dzienny raport ofert\n\n" + "\n\n".join(plain_sections)


def _render_item(
    item: DigestItem,
    *,
    section_key: str,
    position: int,
    feedback_url: str | None,
) -> tuple[str, str]:
    facts = item.candidate.facts
    explanation = item.candidate.explanation
    title = escape(facts.title or facts.id)
    location_text = facts.locality or "Brak danych o lokalizacji"
    location = escape(location_text)
    url = escape(item.listing_url, quote=True)
    score = _score_text(explanation.score)
    score_width = max(Decimal("0"), min(Decimal("100"), explanation.score))
    feedback = (
        f'<a href="{escape(feedback_url, quote=True)}" rel="noreferrer" '
        f'style="color:#334155;margin-left:16px">Oceń ofertę</a>'
        if feedback_url
        else f'<span data-homez-feedback-slot="{section_key}-{position}"></span>'
    )
    criteria_html, criteria_plain = _render_criteria(explanation)
    risk = ""
    risk_plain = ""
    if item.primary_market is not None:
        summary = _display_text(item.primary_market.summary)
        risk = (
            '<p style="background:#fff7ed;padding:10px">'
            f"Ryzyko rynku pierwotnego: {escape(summary)}</p>"
        )
        risk_plain = f"\nRyzyko rynku pierwotnego: {summary}"
    counts = _status_counts(explanation)
    badges = "".join(
        _summary_badge(status, counts[status.key])
        for status in (_MET, _SLIGHT, _UNKNOWN, _STRONG)
        if counts[status.key]
    )
    html = (
        '<article class="listing-card" style="background:#ffffff;border:1px solid '
        '#dbe3ee;border-radius:12px;margin-bottom:24px;padding:20px">'
        '<table role="presentation" style="border-collapse:collapse;width:100%"><tr>'
        f'<td><h3 style="font-size:19px;margin:0 0 6px">{title}</h3>'
        f'<p style="color:#475569;margin:0">{location}</p></td>'
        f'<td style="text-align:right;white-space:nowrap"><strong>Dopasowanie: '
        f"{score}/100</strong></td></tr></table>"
        '<div style="background:#e2e8f0;border-radius:999px;height:7px;'
        "margin:14px 0 12px;"
        'overflow:hidden"><div style="background:#2563eb;height:7px;width:'
        f'{score_width}%"></div></div>'
        f'<p style="margin:12px 0 16px"><a href="{url}" rel="noreferrer noopener" '
        'style="background:#2563eb;border-radius:6px;color:#ffffff;display:inline-block;'
        f'padding:10px 14px;text-decoration:none">Zobacz ogłoszenie</a>{feedback}</p>'
        f'<div style="margin-bottom:16px">{badges}</div>'
        f"{criteria_html}{risk}"
        "</article>"
    )
    plain = (
        "------------------------------------------------------------\n"
        f"{facts.title or facts.id} — {location_text}\n"
        f"Dopasowanie: {score}/100\n{item.listing_url}\n{criteria_plain}{risk_plain}"
    )
    return html, plain


def _render_criteria(explanation: MatchExplanation) -> tuple[str, str]:
    rules = tuple(explanation.eligibility) + tuple(explanation.preferences)
    ordered = sorted(rules, key=lambda rule: _status_order(_status_for(rule)))
    html_rows: list[str] = []
    plain_rows: list[str] = []
    for rule in ordered:
        status = _status_for(rule)
        label = _rule_label(rule.name)
        actual = _display_text(rule.actual)
        threshold = _display_text(rule.threshold)
        distance = _display_text(rule.distance)
        html_rows.append(
            f'<tr data-status="{status.key}" style="border-top:1px solid #e2e8f0">'
            f'<td style="padding:10px 8px">{escape(label)}</td>'
            '<td style="padding:10px 8px"><span style="border-radius:999px;'
            "display:inline-block;"
            f"font-size:12px;font-weight:bold;padding:4px 8px;"
            f"color:{status.foreground};"
            f'background:{status.background}">{status.label}</span></td>'
            f'<td style="padding:10px 8px">{escape(actual)}<br>'
            f'<small style="color:#64748b">{escape(distance)}</small></td>'
            f'<td style="padding:10px 8px">{escape(threshold)}</td></tr>'
        )
        plain_rows.append(
            f"  [{status.label}] {label}: {actual}; wymaganie: {threshold}; {distance}"
        )
    if not html_rows:
        return "", "Brak kryteriów."
    html = (
        '<div style="overflow-x:auto"><table style="border-collapse:collapse;'
        "font-size:14px;"
        'width:100%"><thead><tr style="background:#f8fafc;text-align:left">'
        '<th style="padding:9px 8px">Kryterium</th>'
        '<th style="padding:9px 8px">Status</th>'
        '<th style="padding:9px 8px">Wartość</th>'
        '<th style="padding:9px 8px">Wymaganie</th>'
        f"</tr></thead><tbody>{''.join(html_rows)}</tbody></table></div>"
    )
    return html, "\n".join(plain_rows)


def _status_for(rule: RuleResult) -> _PresentationStatus:
    if rule.state is TriState.PASS:
        return _MET
    if rule.state is TriState.UNKNOWN:
        return _UNKNOWN
    if (
        rule.deviation_ratio is not None
        and rule.deviation_ratio <= _SLIGHT_DEVIATION_LIMIT
    ):
        return _SLIGHT
    return _STRONG


def _status_counts(explanation: MatchExplanation) -> dict[str, int]:
    counts = {status.key: 0 for status in (_MET, _SLIGHT, _UNKNOWN, _STRONG)}
    for rule in (*explanation.eligibility, *explanation.preferences):
        counts[_status_for(rule).key] += 1
    return counts


def _summary_badge(status: _PresentationStatus, count: int) -> str:
    return (
        '<span style="border-radius:999px;display:inline-block;font-size:12px;'
        f"font-weight:bold;margin:0 6px 6px 0;padding:5px 9px;"
        f"color:{status.foreground};"
        f'background:{status.background}">{status.label}: {count}</span>'
    )


def _status_order(status: _PresentationStatus) -> int:
    return {"strong": 0, "met": 1, "slight": 2, "unknown": 3}[status.key]


def _rule_label(name: str) -> str:
    return _RULE_LABELS.get(name, _display_text(name).capitalize())


def _display_text(value: str) -> str:
    if value.casefold() == "unknown":
        return "Brak danych"
    return value.replace("_", " ")


def _score_text(score: Decimal) -> str:
    return format(score.normalize(), "f")


def render_share_text(digest: Digest) -> str:
    """Return copy/mailto content without private feedback URLs or profile data."""
    _, plain = render_digest(digest)
    return plain
