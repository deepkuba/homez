# Krakow Home-Finding Assistant

## Goal

Build a personal decision-support tool that continuously discovers residential
properties for sale in Krakow, learns the buyer's preferences, ranks suitable
listings, and sends a concise weekly email containing the strongest new or
meaningfully changed matches.

The tool assists discovery and comparison. It does not make purchase decisions
or contact sellers without explicit approval.

## Current product hypothesis

The most promising shape is a small, source-adapter-based application:

1. ingest listings through permitted feeds, APIs, alerts, or carefully governed
   browser automation;
2. normalize and deduplicate them into one local catalog;
3. apply hard eligibility filters;
4. score eligible homes using transparent, user-tunable preferences;
5. retain feedback and listing history;
6. email a weekly digest of new, improved, or unusually strong matches.

This is provisional until the discovery questions are answered. In particular,
data-source permissions and the desired hosting model can materially change the
architecture.

## Success criteria (draft)

- The digest contains few enough homes to inspect carefully.
- Most included homes satisfy all non-negotiable constraints.
- Every score can be explained in terms meaningful to the buyer.
- The same property is not presented repeatedly under duplicate advertisements.
- Price and status changes are visible.
- Buyer feedback changes future ranking without silently weakening hard rules.
- Collection complies with source terms, robots policies, privacy requirements,
  and practical rate limits.

## Open discovery questions

1. Is this a private tool running under the buyer's control, or a hosted product
   intended for other users too?
2. What are the non-negotiable buying constraints and the initial budget?
3. Which preferences are trade-offs, and how should they be weighted?
4. How should commute, neighborhood, noise, green space, schools, sunlight,
   renovation state, and legal/ownership risks be evaluated?
5. Which listing sources must be covered, and which access methods do they
   permit?
6. Should the system ingest portal alert emails as a safer alternative to direct
   crawling where no API or feed exists?
7. What should count as "interesting": top score, unusually good value, newly
   listed, reduced price, or a deliberate mix?
8. How many results should a weekly digest contain, and is urgent notification
   needed for exceptional matches?
9. What feedback will the buyer provide: like/dislike, reason codes, saved homes,
   viewed homes, or pairwise comparisons?
10. What ongoing cost, maintenance effort, and cloud dependency are acceptable?

## Documents

- [Glossary](glossary.md)
- [Buyer profile](buyer-profile.md)
- [Budget model](budget-model.md)
- [Catalog history and duplicate review](catalog-history.md)
- [Architecture decision record](adr/0001-delivery-shape.md)
- [Commute-based search area decision](adr/0002-commute-based-search-area.md)
- [Exploration listing decision](adr/0003-exploration-slot.md)
- [Affordability guardrails](adr/0004-affordability-guardrails.md)
- [Renovation-cost treatment](adr/0005-renovation-cost-treatment.md)
- [Title and mortgage transparency](adr/0006-title-and-mortgage-transparency.md)
- [Weekly ranking and slate selection](adr/0007-weekly-ranking-and-selection.md)
- [Managed mailbox decision](adr/0008-managed-mailbox.md)
- [VPS runtime and NAS backup](adr/0009-hosting-topology.md)
- [Python and PostgreSQL application stack](adr/0010-application-stack.md)
- [Routes API quota and failure behavior](adr/0011-routing-quota-guard.md)
- [Implementation plan and slice status](IMPLEMENTATION_PLAN.md)
- [Email feedback security](adr/0012-email-feedback-links.md)
- [Safe sharing and delivery schedule](adr/0013-sharing-and-delivery.md)
- [Primary-market and developer-risk assessment](adr/0014-primary-market-risk.md)
