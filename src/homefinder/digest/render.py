"""Escaped email and share-safe digest representations."""

from dataclasses import dataclass
from datetime import datetime
from html import escape

from homefinder.domain.ranking import RankedCandidate


@dataclass(frozen=True, slots=True)
class DigestItem:
    candidate: RankedCandidate
    listing_url: str


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
    for heading, items in (
        ("Compliant homes", digest.compliant),
        ("Exploration homes", digest.exploration),
    ):
        cards: list[str] = []
        lines = [heading]
        for item in items:
            facts = item.candidate.facts
            explanation = item.candidate.explanation
            title = escape(facts.title or facts.id)
            url = escape(item.listing_url, quote=True)
            feedback = token_urls.get(facts.id)
            feedback_link = (
                f' <a href="{escape(feedback, quote=True)}">feedback</a>'
                if feedback
                else ""
            )
            location = escape(facts.locality or "Location unknown")
            cards.append(
                f"<article><h3>{title}</h3><p>{location} · score "
                f'{explanation.score}</p><a href="{url}" '
                f'rel="noreferrer noopener">open listing</a>{feedback_link}</article>'
            )
            lines.append(
                f"- {facts.title or facts.id} — {facts.locality or 'Location unknown'} "
                f"— {item.listing_url}"
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


def render_share_text(digest: Digest) -> str:
    """Return copy/mailto content without private feedback URLs or profile data."""
    _, plain = render_digest(digest)
    return plain
