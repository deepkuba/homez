# ADR 0001: Delivery shape and acquisition strategy

- Status: Proposed
- Date: 2026-08-30

## Context

The buyer wants a tool that finds Krakow homes for personal purchase, adapts to
their needs, and sends the best matches weekly. The architecture depends on
whether the tool is private or multi-user and on which acquisition methods are
permitted by target listing sources.

Direct scraping is not assumed to be acceptable. Listing pages change often,
anti-automation controls can make collection brittle, and each source can impose
different contractual and technical limits.

## Proposed decision

For a single buyer, prefer a small modular monolith with:

- a scheduled ingestion pipeline;
- one isolated adapter and recorded source policy per source;
- a relational store with listing snapshots and inferred duplicate groups;
- deterministic hard filters and a transparent weighted scoring model;
- explicit buyer feedback events;
- a weekly digest job with idempotent delivery.

Use acquisition methods in this order of preference:

1. documented API or licensed feed;
2. user-configured portal alerts ingested from a dedicated mailbox;
3. other explicitly permitted exports;
4. low-rate browser automation only after source-specific review.

Do not begin with an opaque machine-learning recommender. A rule-based score with
visible reasons is easier to tune with sparse personal feedback and safer for a
high-stakes purchase. More adaptive ranking can be added after enough labeled
examples exist.

## Consequences

- New sources can fail independently and be disabled without breaking ranking.
- Historical snapshots support price-drop alerts and stale-listing detection.
- Email-alert ingestion may provide less complete data but reduces crawler
  fragility and compliance risk.
- Source review and monitoring remain ongoing operational work.
- The initial scoring model needs an explicit preference interview.

## Unresolved decision gates

- Private/local versus hosted/multi-user operation.
- Required sources and approved access method for each.
- Hosting, email provider, geocoding/routing provider, and acceptable monthly cost.
- Retention of seller details and other personal data.
