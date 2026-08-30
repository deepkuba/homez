# ADR 0007: Rank transparently, then select a diversified weekly slate

- Status: Proposed
- Date: 2026-08-30

## Context

The buyer wants 10 preferred/compliant and 10 exploration listings per weekly
email. There may be more than 10 candidates in either pool. Pure score sorting
can fill the report with near-identical apartments from one project, repeatedly
show previously rejected homes, and over-reward listings with more complete
marketing data.

## Decision

Build each section in four stages:

1. **Eligibility and pool:** apply non-overridable rules, separate compliant and
   exploration candidates, deduplicate the same real property, and exclude stale
   or unavailable listings.
2. **Explainable candidate score:** calculate component scores and confidence;
   preserve missing values rather than treating them as zero.
3. **Weekly eligibility:** prioritize new or materially changed properties and
   suppress unchanged listings already shown recently unless explicitly saved.
4. **Diversified slate selection:** select up to 10 per section, balancing raw
   quality with diversity across property, development/building, locality,
   source, price band, and principal reason for selection.

### Initial compliant score (0-100)

| Component | Weight | Examples |
| --- | ---: | --- |
| Affordability and value | 25 | Effective all-in price, mortgage band, price versus credible comparables, recurring costs |
| Commute | 20 | Slower peak-direction duration and margin versus 45 minutes |
| Home and layout fit | 20 | 40 m2 minimum, 48-55 m2 ideal, usable living room plus office/bedroom, separate kitchen |
| Building and immediate environment | 15 | Apartments per entrance, floor/elevator, noise risk, green space |
| Condition and habitability | 10 | Move-in readiness, ability to live during staged work, conservative renovation economics |
| Opportunity and freshness | 5 | Newly listed, meaningful price reduction, corrected or newly available facts |
| Evidence quality | 5 | Address precision, floor plan, fee data, condition evidence, legal-status confidence |

Hard rules are not converted into weights. A high score cannot compensate for a
failed normal-eligibility rule.

Road-noise risk is confirmed as a strong soft penalty within building and
environment scoring, not an automatic rejection.

Direct-owner and agency listings are both eligible. Source type does not receive
an intrinsic preference; affordability/value uses the effective all-in price,
including conservative buyer-side commission where applicable.

Within a component, unknown data should reduce confidence and trigger a question;
it should not automatically receive the same penalty as a known bad fact. Every
listing must show its component breakdown and strongest positive/negative reasons.

### Exploration score and selection

Exploration results are not merely candidates ranked 11-20. First exclude rentals,
lack of vacant possession, and known serious title risk. Then favor candidates
that:

- narrowly miss one or a small number of normal filters;
- offer exceptional value or a distinctive benefit;
- test different assumptions rather than breaking the same rule ten times;
- have sufficient evidence to make the trade-off understandable;
- have not already received negative feedback without a material change.

The exploration section should diversify the main broken rule—for example legal
form, commute, building size, area, floor, condition, or geography. It must name
the failed rules and distance from thresholds.

### Ordering within the email

The selected compliant section is ordered by candidate score after slate
selection. The selected exploration section is ordered by expected usefulness,
not by pretending ineligible homes have compliant scores. Each card states why it
was selected this week.

## Repetition and change rules

- Never show duplicate advertisements for the same inferred property as separate
  recommendations.
- Do not resurface an unchanged dismissed listing during a configurable cooldown.
- Resurface a known listing when price, availability, renovation facts, title
  information, or another material input changes.
- Keep saved/watchlisted properties in a separate status summary rather than
  consuming all new-discovery positions.
- If fewer than 10 worthwhile candidates exist, send fewer; do not pad the email.

## Consequences

- The email covers more of the buyer's opportunity space than a raw top-10 list.
- Scores remain inspectable and initially tunable without machine learning.
- Slate selection needs stable property identity, history, and feedback.
- Initial weights are hypotheses and require buyer approval plus later feedback;
  learning may adjust soft preferences but never hard rules automatically.
