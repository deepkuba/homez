# ADR 0015: Isolate listing-page retrieval on the NAS

- Status: Accepted
- Date: 2026-09-07

## Context

Email alerts provide canonical offer links but may omit descriptions and mutable
facts needed for useful matching. Page retrieval has different reliability,
network, and source-governance risks from Gmail ingestion. The buyer requested
that retrieval run on the NAS as separate processes.

## Decision

Run one source-pinned process per portal on the NAS. Each process validates an
exact HTTPS listing host and path, performs a bounded non-redirecting GET, and
returns normalized structured data through an authenticated HTTP endpoint bound
only to the NAS Tailscale address. The VPS workflow calls these endpoints over
the encrypted tailnet. It never exposes PostgreSQL or grants NAS processes Gmail,
mail-delivery, feedback, or database credentials.

Page retrieval remains disabled per source unless `page_fetch_enabled` is the
literal boolean `true`. A process respects portal denial responses and does not
implement CAPTCHA or anti-bot bypasses.

## Consequences

- A portal scraper can be restarted or disabled independently.
- The VPS is still responsible for durable jobs, retries, matching, and reports.
- The NAS and VPS share one dedicated scraper token, rotated independently of all
  other application credentials.
- Source HTML drift produces a retryable workflow failure instead of silently
  inventing facts.
- Residential NAS egress may improve ordinary page availability, but it does not
  justify bypassing a portal's controls or terms.
