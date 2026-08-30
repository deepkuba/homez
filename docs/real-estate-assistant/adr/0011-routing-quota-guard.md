# ADR 0011: Keep Routes API usage below the free allowance

- Status: Accepted
- Date: 2026-08-30

## Context

The buyer accepts a billing-enabled Google Cloud project but requires the system
never to exceed the free Routes API allowance. When the limit is reached, the
buyer wants an email notification.

Google bills Compute Routes by request and Compute Route Matrix by element. Free
allowances and SKU classification can change. Cloud budget alerts do not by
themselves stop spending, and Google notes possible discrepancies between quota
and billing metrics.

## Decision

Use defense in depth:

1. Put Routes API in a dedicated Google Cloud project with no unrelated callers.
2. Restrict the API credential to the required API and the VPS egress address or
   other strongest supported application restriction.
3. Configure Google-side quota limits below the applicable free allowance where
   the available quota dimensions permit it.
4. Maintain a PostgreSQL quota ledger per calendar month, provider, method, and
   billable SKU/unit.
5. Before a request, atomically reserve its worst-case billable units. Parallel
   workers cannot reserve past the configured application ceiling.
6. Set the application ceiling below the published allowance, initially 90%, to
   cover counting differences, retries, in-flight requests, and administrative
   tests.
7. Cache and batch permitted results, route only plausible candidates, and avoid
   recomputing unchanged origin/destination/time assumptions.
8. Treat provider `RESOURCE_EXHAUSTED`/quota responses as a hard circuit-breaker,
   not as an endlessly retried transient failure.

The configured free allowance and safety ceiling must be versioned operational
settings with source URL and verification date. A monthly job must not increase
them automatically; increases require explicit review of current pricing and
terms.

## Limit notification and degraded behavior

When the local safety ceiling or provider quota is reached:

- stop issuing new route requests immediately;
- send one Gmail notification per incident/month stating the period, configured
  allowance, used/reserved units, affected method/SKU, and queued candidates;
- mark route-dependent candidates as `routing pending: quota exhausted`;
- continue ingestion, normalization, non-route enrichment, and database backups;
- generate the weekly report from still-valid cached routes, visibly label stale
  route data, and do not claim that an unrouted candidate passes the 45-minute
  hard rule;
- resume automatically only after the next quota period begins or an operator
  explicitly resolves a configuration/provider error.

The quota alert uses Gmail and therefore remains independent of Routes API.

## Monitoring

- Alert at 70%, 85%, and the application ceiling.
- Record actual provider responses and reconcile local counts against Cloud
  metrics.
- Expose remaining units and oldest pending route in the health status.
- Test quota exhaustion without calling Google by using a fake provider.

## Consequences

- Routing may become temporarily incomplete rather than incur paid usage.
- A safety margin intentionally leaves part of the nominal free allowance unused.
- Correct SKU/unit accounting is part of the provider adapter's acceptance tests.
- Absolute protection also depends on Google-side restrictions and preventing
  other use of the dedicated project/key.

## Current official references

- Routes API usage, per-request/per-element billing, and quotas:
  https://developers.google.com/maps/documentation/routes/usage-and-billing
- Google Maps cost and quota controls:
  https://developers.google.com/maps/billing-and-pricing/manage-costs
- Cloud Billing warning that alert-only budgets do not cap spending:
  https://docs.cloud.google.com/billing/docs/how-to/budgets
