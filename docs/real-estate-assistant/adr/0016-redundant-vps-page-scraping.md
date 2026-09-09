# ADR 0016: Add VPS page scrapers as controlled secondaries

- Status: Accepted
- Date: 2026-09-09

## Context

The source-pinned NAS processes isolate portal retrieval from the application
VPS, but NAS or tailnet unavailability can prevent normalization. Replacing the
NAS path would discard that isolation and residential egress. Sending every
request to both deployments would duplicate portal traffic and maintain two
independent daily-limit counters.

## Decision

Keep each NAS endpoint as the primary and run four additional source-pinned
scraper processes on the VPS. The workflow uses the matching VPS process only
when the primary transport is unavailable or the primary service returns a
server-side failure. It does not fail over after a portal cooldown, `403`,
`429`, another client error, or an invalid response.

VPS scrapers publish no host ports, have no database network or credentials,
and receive only the shared scraper token. A private control network connects
them to the workflow worker; a separate bridge provides outbound portal access.
Each process keeps its own persistent rate-limit state.

## Consequences

- NAS remains active and automatically becomes the path used again when it
  recovers, because every new scrape tries the primary first.
- VPS failover improves availability without routinely doubling portal traffic.
- An ambiguous primary transport failure can still result in one repeated
  portal request; persistent pacing and workflow retries limit this risk.
- Removing the VPS overlay restores the NAS-only topology without a schema or
  data rollback.
