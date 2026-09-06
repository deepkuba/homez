"""Escaped email and share-safe digest representations."""

from dataclasses import dataclass
from datetime import datetime
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


def render_digest(
    digest: Digest, *, token_urls: dict[str, str] | None = None
) -> tuple[str, str]:
    token_urls = token_urls or {}
    sections: list[str] = []
    plain_sections: list[str] = []
    for heading, section_key, items in (
        ("Compliant homes", "compliant", digest.compliant),
        ("Exploration homes", "exploration", digest.exploration),
    ):
        cards: list[str] = []
        lines = [heading]
        for position, item in enumerate(items, start=1):
            facts = item.candidate.facts
            explanation = item.candidate.explanation
            title = escape(facts.title or facts.id)
            url = escape(item.listing_url, quote=True)
            feedback = token_urls.get(facts.id)
            feedback_link = (
                f' · <a href="{escape(feedback, quote=True)}" '
                'rel="noreferrer">feedback</a>'
                if feedback
                else (
                    f'<span data-homez-feedback-slot="{section_key}-{position}"></span>'
                )
            )
            criteria_html, criteria_plain = _render_criteria(explanation)
            risk = ""
            risk_plain = ""
            if item.primary_market is not None:
                dossier = item.primary_market
                risk = f"<p>Primary-market risk: {escape(dossier.summary)}</p>"
                risk_plain = f" — primary-market risk: {dossier.summary}"
            location = escape(facts.locality or "Location unknown")
            cards.append(
                f"<article><h3>{title}</h3><p>{location} · score "
                f'{explanation.score}</p><a href="{url}" '
                f'rel="noreferrer noopener">open listing</a>{feedback_link}'
                f"{criteria_html}{risk}</article>"
            )
            lines.append(
                f"- {facts.title or facts.id} — {facts.locality or 'Location unknown'} "
                f"— {item.listing_url}{risk_plain}\n{criteria_plain}"
            )
        sections.append(
            f"<section><h2>{heading}</h2>{''.join(cards) or '<p>None</p>'}</section>"
        )
        plain_sections.extend(lines)
    html = (
        '<!doctype html><meta name="referrer" content="no-referrer"><main>'
        f"<h1>Homefinder weekly digest</h1>{''.join(sections)}</main>"
    )
    return html, "Homefinder weekly digest\n\n" + "\n".join(plain_sections)


def _render_criteria(explanation: MatchExplanation) -> tuple[str, str]:
    groups = (
        ("Criteria met", TriState.PASS),
        ("Criteria not met", TriState.FAIL),
        ("Unknown / needs verification", TriState.UNKNOWN),
    )
    html_groups: list[str] = []
    plain_groups: list[str] = []
    for heading, state in groups:
        rules = tuple(rule for rule in explanation.eligibility if rule.state is state)
        html_rules = "".join(f"<li>{escape(_rule_text(rule))}</li>" for rule in rules)
        html_groups.append(
            f"<section><h4>{heading}</h4>"
            f"{f'<ul>{html_rules}</ul>' if rules else '<p>None</p>'}</section>"
        )
        plain_groups.append(
            f"  {heading}: "
            + ("; ".join(_rule_text(rule) for rule in rules) if rules else "None")
        )
    return f'<div class="criteria">{"".join(html_groups)}</div>', "\n".join(
        plain_groups
    )


def _rule_text(rule: RuleResult) -> str:
    return (
        f"{rule.name}: actual {rule.actual}; threshold {rule.threshold}; "
        f"{rule.distance}"
    )


def render_share_text(digest: Digest) -> str:
    """Return copy/mailto content without private feedback URLs or profile data."""
    _, plain = render_digest(digest)
    return plain
