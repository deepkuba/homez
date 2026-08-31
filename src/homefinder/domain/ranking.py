"""Deterministic, diversified compliant and exploration slate selection."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from homefinder.domain.matching import MatchExplanation, PropertyFacts, evaluate
from homefinder.domain.profile import BuyerProfile


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    facts: PropertyFacts
    explanation: MatchExplanation


@dataclass(frozen=True, slots=True)
class Slate:
    compliant: tuple[RankedCandidate, ...]
    exploration: tuple[RankedCandidate, ...]


def select_slate(
    facts: list[PropertyFacts],
    profile: BuyerProfile,
    limit: int = 10,
    *,
    now: datetime | None = None,
    cooldown: timedelta = timedelta(days=7),
) -> Slate:
    selected_at = now or datetime.now(timezone.utc)
    ranked = sorted(
        (
            RankedCandidate(item, evaluate(item, profile))
            for item in facts
            if _can_present(item, selected_at, cooldown)
        ),
        key=lambda item: (-item.explanation.score, item.facts.id),
    )
    compliant = _diversify(
        [item for item in ranked if item.explanation.eligible], limit
    )
    exploration = _diversify(
        [
            item
            for item in ranked
            if not item.explanation.eligible
            and (
                item.facts.transaction_type is None
                or item.facts.transaction_type != "rental"
            )
            and item.facts.serious_legal_risk is not True
            and item.facts.vacant_possession is not False
        ],
        limit,
    )
    return Slate(compliant, exploration)


def _diversify(
    candidates: list[RankedCandidate], limit: int
) -> tuple[RankedCandidate, ...]:
    """Prefer one candidate per locality before filling remaining slots."""
    selected: list[RankedCandidate] = []
    remaining = list(candidates)
    localities: set[str] = set()
    for candidate in candidates:
        locality = candidate.facts.locality or "unknown"
        if locality.casefold() in localities:
            continue
        selected.append(candidate)
        remaining.remove(candidate)
        localities.add(locality.casefold())
        if len(selected) == limit:
            return tuple(selected)
    selected.extend(remaining[: max(0, limit - len(selected))])
    return tuple(selected)


def _can_present(facts: PropertyFacts, now: datetime, cooldown: timedelta) -> bool:
    if facts.last_presented_at is None or facts.materially_changed:
        return True
    presented_at = facts.last_presented_at
    if presented_at.tzinfo is None:
        presented_at = presented_at.replace(tzinfo=timezone.utc)
    return now - presented_at >= cooldown
