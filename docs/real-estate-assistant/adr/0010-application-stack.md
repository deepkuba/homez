# ADR 0010: Use Python, PostgreSQL, and a modular monolith

- Status: Accepted
- Date: 2026-08-30

## Context

The buyer is comfortable with Python and PostgreSQL. The system combines email
ingestion, HTML/data extraction, geospatial checks, explainable ranking, scheduled
jobs, and report generation for one user.

## Decision

Use:

- Python for the application;
- PostgreSQL for durable state and listing history;
- PostGIS for coordinates, proximity, spatial indexing, and geographic features;
- SQLAlchemy plus Alembic for persistence and schema migrations;
- a lightweight scheduler or system cron for inbox polling, enrichment, backups,
  and weekly report generation;
- a small HTTP/admin interface only where OAuth callbacks, health checks, or
  feedback links require it;
- Playwright in an isolated worker only for sources whose reviewed access method
  requires browser rendering.

Keep ingestion adapters, normalization/deduplication, enrichment, eligibility,
ranking, feedback, and delivery as modules in one deployable application. Use
database-backed job state and idempotency rather than introducing a task queue at
the outset.

## Initial boundaries

- `sources`: email and source-specific listing adapters;
- `catalog`: normalized listings, snapshots, property candidates, and duplicate
  evidence;
- `enrichment`: geocoding, routing, noise/green-space/building/renovation facts;
- `profile`: versioned constraints and preferences;
- `ranking`: eligibility, component scores, confidence, and slate selection;
- `digest`: HTML/text report generation, feedback links, and Gmail delivery;
- `operations`: schedules, source health, retries, audit records, and backups.

External-API calls must go through provider-specific clients that enforce local
quota reservation, caching, retry, and circuit-breaker rules; application modules
must not call provider SDKs directly.

## Consequences

- The stack is straightforward to operate on one VPS and easy to reproduce on the
  NAS.
- PostgreSQL supports both transactional history and geospatial queries.
- A task queue, distributed cache, separate frontend, or machine-learning service
  should be added only when measured needs justify it.
- All dependency versions will be pinned during implementation rather than fixed
  prematurely in the design record.
