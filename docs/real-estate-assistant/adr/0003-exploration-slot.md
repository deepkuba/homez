# ADR 0003: Balance compliant and exploration listings

- Status: Proposed
- Date: 2026-08-30

## Context

Strict filters can encode assumptions that the buyer has not tested. A digest
containing only eligible homes cannot reveal a valuable trade-off just outside
the current boundary.

The buyer initially requested one such listing and then expanded the requirement:
every report should contain as many exploration listings as preferred/compliant
listings.

## Decision

Use a 1:1 ratio: one **exploration listing** for every preferred/compliant listing.
Keep exploration results in a separate section after compliant matches. For each
one show:

- every failed and uncertain filter;
- the amount by which each threshold is missed;
- exceptional strengths and effective all-in price;
- why it was selected instead of other near misses;
- a one-click feedback choice: keep the rule, soften it, or discuss later.

Rank exploration candidates by a combination of near-miss distance, exceptional
value/quality, novelty, and diversity from previously shown exploration listings.
Do not simply show the highest-scoring rejected home every week.

## Guardrails

- Never describe an exploration listing as a normal match.
- Unknown information is not the same as a failed filter.
- Avoid repeating a rejected exception unless price or facts materially change.
- Do not automatically modify the buyer profile from one reaction.
- The exploration listing may break ordinary property, location, commute, and
  price filters. In particular, Skawina is excluded from normal results but is
  allowed in this slot.
- Cooperative ownership rights are excluded from normal results but allowed in
  this section when clearly identified and not accompanied by a known serious
  legal-title risk.
- It may never be a rental, lack vacant possession, or carry a known serious
  legal-title risk.

## Consequences

- Reports remain useful when the eligible set is empty.
- The buyer can discover which constraints carry real trade-offs.
- Half of a normal digest is deliberately allocated to breadth rather than strict
  fit, increasing discovery at the cost of fewer compliant results for a fixed
  total email length.
- Digest evaluation must separately measure compliant-match quality and
  exploration usefulness.
- The system needs reasoned rejection data and explicit feedback actions.
