# ADR 0017 implementation plan

- Status: Ready for implementation after ADR review
- Decision: [ADR 0017](0017-concurrent-scrape-workers.md)
- Scope: repository implementation and staged rollout; no automatic production
  activation

## Outcome

Replace the synchronous NAS-primary/VPS-fallback page fetch with a central,
source-aware queue consumed concurrently by source-pinned NAS and VPS workers.
Add bounded Webshare egress, independent portal parsers, NAS-only diagnostic
artifacts, an offline candidate benchmark lane, manual parser activation and
rollback, newest-capture recovery, and enforced data retention without changing
report selection or rendering.

The implementation must sustain a simulated capacity of at least 1,000
successful page downloads per portal per day and keep eligible live tasks within
the 10-minute P95 target under the documented 45-task burst. Real traffic remains
bounded by the reviewed policy for each portal.

## Current repository delta

The existing code provides useful foundations but not the required boundaries:

- `WorkflowRepository` has fenced leases, but all job kinds share one claim path
  and workers are not capability- or source-scoped.
- `WorkflowService` performs page retrieval synchronously inside `normalize`.
- `RemotePortalScraper` treats NAS as primary and VPS as fallback.
- NAS and VPS scraper processes enforce independent 150-request state files, so
  aggregate pacing is not coordinated.
- `PortalPageScraper` is one generic parser and returns structured JSON without
  preserving a page-capture/result boundary.
- `CandidateFactSetRecord` records only a normalizer string, not immutable parser
  release, activation epoch, field candidates, or page-capture provenance.
- The error dashboard already has authenticated newest-first pagination and SSE;
  it should be extended rather than replaced.
- `fixture_safety` scans email fixtures only.
- Backup tooling exists, but backup manifests, restore retention warnings,
  production-result purge, and size/health alerts do not.

## Implementation assumptions

- PostgreSQL on the VPS remains the authoritative coordinator and production
  metadata store; raw object bytes remain on NAS.
- The current single application image may initially bundle active, rollback,
  and candidate parser releases. Runtime selection still uses content-addressed
  release hashes, and worker heartbeats prove which hashes are executable before
  activation.
- Every new component and migration ships dark behind validated configuration or
  an inactive release pointer until its slice exit conditions pass.
- Ten-gigabyte alert boundaries use decimal GB consistently in storage metrics.
- Existing authenticated mail delivery can carry operational alerts through a
  separate template containing no listing content.
- Real page variants and raw activation evidence cannot be fabricated. Slices
  0–11 can complete with fakes and synthetic fixtures; portal release eligibility
  remains pending until authorized production artifacts exist.
- A reviewed portal policy that cannot pass the reference 45-task burst means the
  10-minute SLO is explicitly unavailable for that portal; code must not weaken
  denial handling to manufacture compliance.

## Non-negotiable implementation boundaries

1. Live production runs only the active parser release. Candidate code never
   parses live traffic or writes production facts.
2. The candidate benchmark runs only on one resource-limited NAS worker and
   performs no network requests.
3. NAS and VPS production workers claim distinct tasks concurrently. They never
   receive PostgreSQL credentials.
4. Aggregate portal limits, cooldowns, proxy bytes, and route health are central;
   adding workers cannot multiply traffic.
5. Raw page bytes are capped at 2 MB, encrypted on NAS only, excluded from
   backups, and never written on VPS.
6. Gratka, Morizon, Otodom, and OLX own separate detector, extraction,
   normalization, fixture, and quality code. Do not introduce shared parser
   helpers; intentionally copy small parsing logic when useful.
7. Partial results remain eligible for the existing downstream flow. Do not add
   an incomplete-report section or change report ranking/rendering.
8. Parser activation and rollback are manual, portal-specific, audited, atomic,
   and fenced by activation epoch.
9. Detailed benchmark values expire with their raw artifacts. Production parsed
   results expire two years after the page capture's `fetched_at`.
10. Tests use synthetic fixtures and fake transports. No CI or developer test may
    contact a real portal or Webshare.

## Target module boundaries

The exact filenames may change during implementation, but preserve these
ownership seams:

```text
src/homefinder/
  scrape_queue/       # coordinator task state, leases, worker capabilities
  scraper/            # bounded transport, direct/proxy execution, worker runtime
  artifacts/          # NAS encrypted store, service API, retention, audit
  parsers/
    contracts.py       # shared input/result boundary only
    gratka/            # independent detector, variants, normalization
    morizon/
    otodom/
    olx/
  parser_releases/    # immutable registry, activation epoch, rollback
  benchmark/          # manifests, worker, comparisons, reviews; no network client
  operations/         # production retention and storage/health alerts
  web/                # live errors, parser quality, parser versions
```

The existing `listing_snapshots` remain observations of listing/catalog state.
Add a separate immutable `page_captures` concept for bounded HTTP responses and
their `fetched_at`. Parser results reference both the listing snapshot they enrich
and the page capture they consumed. A fresh recovery request creates a new page
capture; artifact replay does not.

## Persistence plan

Use backward-compatible Alembic migrations. Prefer expand/migrate/contract over
renaming or deleting existing columns in one release.

### Queue and network state

- `scrape_tasks`: source, listing/snapshot identity, URL, priority, task class,
  pinned parser release/epoch, state, availability, idempotency key.
- `scrape_attempts`: fenced lease, worker, opaque route class/identifier, byte
  counts, response classification, timing, and bounded safe error data.
- `scraper_workers`: deployment (`nas` or `vps`), source, heartbeat, supported
  release hashes, and health; no secrets or addresses.
- `source_runtime_state`: central interval, attempt/success counters, denial
  cooldown, and policy version.
- `proxy_usage_ledger` and `proxy_route_health`: billing-cycle bytes, 900 MB
  usable ceiling, reservation, opaque route state, and source-pair quarantine.

### Captures, parser results, and artifacts

- `page_captures`: listing snapshot, final canonical identity, HTTP metadata,
  `fetched_at`, content hash, size, route class, and outcome. Do not store raw
  response bytes in PostgreSQL.
- `parser_releases` and `portal_parser_activations`: immutable build provenance,
  lifecycle state, active pointer, activation epoch, actor, and audit time.
- `production_parser_results`: capture, active release, activation epoch,
  variant, completeness, normalized result, retention deadline, and immutable
  result hash. It contains no candidate-benchmark row.
- `production_field_candidates`: field, normalized value, semantic origin,
  structural locator, resolution, and conflict state for production results.
- `artifact_metadata`, `diagnostic_runs`, `quality_sample_reviews`, and
  `artifact_tombstones`: NAS object reference and safe metadata only.

### Benchmark, recovery, and retention

- `benchmark_runs`, `benchmark_manifest_entries`, `benchmark_results`, and
  `benchmark_field_candidates`: physically separate candidate-benchmark
  persistence for frozen corpus, active/candidate output, progress, unavailable
  inputs, and safe retained metrics. No production-result foreign key or write
  path accepts these rows as effective facts.
- `benchmark_difference_clusters` and `benchmark_reviews`: signature,
  representative, coding-agent assessment, reason, and eligibility effect.
- `retention_job_runs`, `database_size_alert_state`, and
  `listing_retention_tombstones`: daily purge audit, per-boundary seven-day
  re-arm state, health reminders, and indefinite non-content listing identity.

Do not copy raw artifact content, proxy credentials, authorization headers, or
cookies into any of these tables.

## Acceptance criteria

### Queue and throughput

- **AC-Q1:** Simultaneous NAS and VPS claims for one portal return different
  tasks; exactly one fenced result is accepted per task.
- **AC-Q2:** Worker loss expires the lease; a late result from the old token or
  activation epoch is rejected.
- **AC-Q3:** New live tasks outrank artifact and network recovery. Benchmark work
  is on a separate queue and cannot acknowledge production work.
- **AC-Q4:** A virtual-clock capacity test completes 1,000 successful tasks for
  each portal in one day and meets the 10-minute P95 for an eligible 45-task
  single-portal burst using the reference profile of one aggregate start per 10
  seconds and successful responses within 10 seconds.

### Pacing and egress

- **AC-N1:** All workers share one portal interval, attempt/success ceiling, and
  direct-denial cooldown.
- **AC-N2:** Proxy infrastructure failure permits one immediate direct attempt.
  Proxy portal denial waits at least 15 minutes and then permits one direct
  attempt. Direct denial backs off 6, 12, then 24 hours.
- **AC-N3:** A proxy denial never hops to another proxy. A `Retry-After` longer
  than the configured delay wins.
- **AC-N4:** Webshare allocation stops at 900 MB per billing cycle and direct
  work continues. Logs contain no proxy endpoint or credential.
- **AC-N5:** Cross-source redirects produce an idempotent handoff and never feed
  target content to the source parser.

### Parsing and artifacts

- **AC-P1:** Each portal has an independent positive variant detector and exactly
  one selected handler; zero or multiple matches produce `unknown-variant`.
- **AC-P2:** Every declared field has candidates, provenance, and an explicit
  value/unknown/ambiguous outcome. Page summary/attributes outrank description;
  page values outrank email fallback.
- **AC-P3:** Any missing declared field creates one deduplicated artifact with a
  complete `missing_fields` list while partial normalization continues.
- **AC-P4:** `price_per_sqm_minor` prefers an explicit summary value, otherwise is
  derived only from page price and area. Differences up to 100 PLN/m² are
  equivalent; configured low/normal/high boundaries validate and filter.
- **AC-A1:** Artifact bytes are exact bounded parser input, encrypted with a
  per-object key on NAS, absent from VPS disk/logs/backups, and unavailable after
  automatic or acknowledged manual deletion.
- **AC-A2:** NAS artifact outage completes the production task with
  `artifact-unavailable` and never triggers a duplicate page request.
- **AC-A3:** The maintenance API exposes paginated safe metadata and one audited
  raw read only. The thin CLI cannot list/download raw content in bulk, write
  facts, generate fixtures, or activate parsers.
- **AC-A4:** Manually authored HTML/JSON fixtures fail the same safety scanner
  locally and in CI when they contain active URLs, personal/contact data,
  secrets, unparsed content, or unsafe executable markup.

### Benchmark and releases

- **AC-B1:** Active and candidate parsers receive identical frozen manifest
  bytes; a benchmark has no network capability and at most 1,000 raw artifacts
  per portal plus all applicable fixtures.
- **AC-B2:** Every changed variant has at least one readable raw example. Every
  new difference signature has a reviewed representative. Incorrect, unreviewed,
  or value-producing ambiguous behavior blocks eligibility.
- **AC-B3:** Any parser/config/dependency change creates a new content-addressed
  candidate and reruns the complete benchmark.
- **AC-R1:** Activation requires eligible benchmark evidence and healthy NAS and
  VPS workers advertising the candidate and rollback release hashes.
- **AC-R2:** Manual activation changes one portal pointer atomically. Manual
  rollback restores the previous release without a benchmark; late withdrawn-
  epoch results are rejected.
- **AC-R3:** Effective reads exclude `revoked` results but may use valid
  `retired` results. History remains immutable.

### Recovery, retention, and operations

- **AC-X1:** Activation automatically reparses qualifying newest page captures
  from retained artifacts without network access; it never reparses historical
  captures.
- **AC-X2:** Recovery lacking an artifact requires manual 50/150/remainder batch
  release and shares normal portal/proxy limits.
- **AC-T1:** Raw artifacts expire after 30 days. Detailed raw-derived benchmark
  output expires with its artifact. Production parser results expire two years
  after capture `fetched_at`; replay cannot extend either deadline.
- **AC-T2:** Daily bounded purge retains only non-content aggregates and minimal
  listing tombstones. Only confirmed inactive tombstones block future downloads.
- **AC-T3:** Database-size mail fires at each crossed 10 GB boundary, re-arms
  after seven successful below-boundary days, and contains no listing content.
- **AC-T4:** Retention failure alerts immediately, reminds at most daily, and
  sends one recovery notification. Restore shows dates and a non-blocking
  retention warning; backup deletion remains manual.
- **AC-O1:** Dashboard retains newest-first live errors/load-more and adds parser
  quality and parser-version views with authorization, escaped raw display, and
  no cached raw response.

### Acceptance-to-test mapping

| Criteria | Primary slice and test level |
| --- | --- |
| AC-Q1–Q3 | Slices 1–2, PostgreSQL concurrency and worker integration |
| AC-Q4 | Slice 3, virtual-clock capacity/load test |
| AC-N1–N5 | Slice 3, repository concurrency plus fake transport contracts |
| AC-P1–P4 | Slices 5–6, portal unit fixtures and persistence integration |
| AC-A1–A4 | Slices 4, 6, and 9, crypto/filesystem/API/security tests |
| AC-B1–B3 | Slice 8, isolated benchmark unit and PostgreSQL integration |
| AC-R1–R3 | Slice 7, activation concurrency and effective-read integration |
| AC-X1–X2 | Slice 10, recovery scheduling and dry-run migration integration |
| AC-T1–T4 | Slice 11, frozen-clock retention/backup/alert integration |
| AC-O1 | Slice 9, authenticated web, SSE, pagination, and XSS tests |

## Test-first implementation slices

Each slice begins with the named failing test, ends with the narrowest full
verification appropriate to its blast radius, and receives its own commit.

### Slice 0 — Contract and migration skeleton

Status: repository implementation complete (2026-09-09); PostgreSQL and image
verification remain CI gates.

Evidence:
- Named architecture test failed on the absent benchmark boundary before any
  production edits; additional contract/migration tests failed on absent flags,
  bounded input, task classes, and tables.
- Added inert portal, benchmark, artifact, and queue packages, typed protocols,
  off-by-default settings, and expand-only migration `20260909_22`.
  Catalog snapshots remain separate from page captures. No live dispatch,
  parser activation, network request, or report behavior was changed.
- Architecture guard follows absolute and relative imports transitively; its
  negative test first demonstrated and then closed an indirect-network gap.
- Focused tests passed; full suite: 249 passed, 7 PostgreSQL tests skipped before
  the additional architecture regression (subsequent targeted suite passed).
- Ruff format/check, strict mypy, dependency audit, YAML lint, fixture-safety
  scanner, SQLite upgrade and Alembic schema-drift check passed.
- Base, combined shared-VPS/scraping/local-worker, and NAS Compose configurations
  passed using installed Compose v1.29.2. Compose v2 is absent; Docker daemon
  access is denied and noninteractive sudo requires a password. No local
  PostgreSQL server/PostGIS or `TEST_POSTGRES_URL` is available. PostgreSQL
  integration and immutable-image build are unverified, not counted as passes.
- Diff review: no secrets, raw source content, fixtures, debug output, or
  unrelated implementation changes. Pre-existing main-plan/glossary edits and
  ADR/prompt documents remain untouched.
- Reassessment for Slice 1: implement repository/API state separately from the
  worker contracts; require a disposable PostGIS database in CI for concurrent
  claim and stale-token proof. No production database may be used for tests.
  All deployment inputs listed below remain pending; no live action authorized.


First failing test:
`tests/architecture/test_scraping_boundaries.py::test_candidate_benchmark_has_no_network_dependency`.

- Add package boundaries, shared transport/result protocols, task classes, and
  feature flags defaulting the new path off.
- Add architecture tests forbidding imports from benchmark to network clients,
  from source parser packages to one another, and from workers to catalog ORM.
- Add the first expand-only migration for page captures, queue identity, and
  parser release identifiers without changing live behavior.

Exit: current tests pass with the legacy fallback still active and new features
disabled.

Commit: `feat(scraping): establish concurrent pipeline boundaries`

### Slice 1 — Central coordinator and fenced scrape queue

First failing test:
`tests/integration/test_scrape_queue_postgres.py::test_nas_and_vps_claim_distinct_tasks`.

- Implement source/task-class-scoped enqueue, claim, heartbeat, defer, succeed,
  fail, lease expiry, and idempotency in PostgreSQL.
- Expose a private authenticated coordinator API for workers; keep PostgreSQL
  inaccessible to them.
- Persist safe attempts and worker capability heartbeats.
- Add cursor-based queue status queries needed by operations.

Exit: concurrent transaction tests prove distinct claims and stale-token
rejection under PostgreSQL.

Commit: `feat(scraping): add fenced central scrape queue`

### Slice 2 — Concurrent NAS and VPS worker runtime

First failing test:
`tests/integration/test_concurrent_scrape_workers.py::test_both_deployments_complete_shared_queue_work`.

- Add one source-pinned long-running worker command used on NAS and VPS.
- Split bounded transport from parsing and return a typed page-capture outcome.
- Change workflow normalization to enqueue and await a scrape outcome rather
  than synchronously call primary/fallback endpoints.
- Add Compose services and health checks without removing the legacy path yet.
- Prove graceful shutdown, lease heartbeat, and no local database credentials.

Exit: fake transport demonstrates concurrent work stealing with no duplicate
portal request or result.

Commit: `feat(scraping): run NAS and VPS workers concurrently`

### Slice 3 — Central pacing, denial policy, and Webshare routing

First failing test:
`tests/integration/test_source_budget_postgres.py::test_two_workers_share_one_portal_budget`.

- Implement transactional per-source interval, attempt/success ledgers, and
  source-wide cooldown state.
- Implement opaque health-based proxy assignment, compressed byte reservation,
  900 MB usable ceiling, billing-cycle reset, and direct fallback when exhausted.
- Classify infrastructure failure versus portal denial and implement the exact
  immediate/15-minute/6-to-24-hour policy.
- Add redirect handoff and denial/cooldown metrics.
- Add virtual-clock throughput and burst tests; do not call real endpoints.

Exit: concurrency cannot exceed aggregate limits, and every denial branch is
covered by deterministic tests.

Commit: `feat(scraping): coordinate source and proxy budgets`

### Slice 4 — NAS artifact service and diagnostic outcomes

First failing test:
`tests/integration/test_artifact_service.py::test_vps_streams_failure_bytes_without_local_persistence`.

- Add NAS encrypted object storage with per-artifact data keys, wrapped NAS-only
  keys, content-hash deduplication, 2 MB cap, and exact parser-input bytes.
- Add authenticated worker upload and benchmark/maintenance read scopes.
- Add diagnostic metadata, `missing_fields`, quality-sample reservoir, audit,
  automatic 30-day crypto-shred, idempotent manual deletion, and tombstones.
- Implement `artifact-unavailable` without refetch or VPS fallback storage.
- Exclude object paths and keys from backup configuration and tests.

Exit: filesystem, crypto, outage, concurrent dedupe, expiry, and no-log/no-backup
tests pass.

Commit: `feat(artifacts): retain bounded parser diagnostics on NAS`

### Slice 5 — Versioned production parser-result boundary

First failing test:
`tests/integration/test_parser_results_postgres.py::test_partial_page_result_keeps_field_provenance`.

- Add immutable parser results and field candidates linked to `page_captures`.
- Implement eleven core fields, explicit unknown/ambiguous states, origin
  precedence, page-over-email effective facts, and diagnostic linkage.
- Add `price_per_sqm_minor`, 100 PLN/m² consistency, and configurable
  low/normal/high criteria boundaries.
- Adapt downstream `CandidateFactSetRecord` creation without changing report
  behavior.

Exit: partial results continue downstream, conflicts remain visible, and the
existing report snapshots do not change except for newly available facts.

Commit: `feat(parsing): persist versioned partial page results`

### Slice 6 — Independent portal parser packages and fixture safety

First failing test:
`tests/architecture/test_scraping_boundaries.py::test_portal_parsers_share_no_extraction_code`.

- Move the current baseline behavior behind four independent portal packages.
- Add positive variant detection and `unknown-variant`; do not improve all portal
  selectors in this slice.
- Extend `fixture_safety` to bounded `.html` and `.json` files and wire the same
  command into local documentation and CI.
- Add minimal manually authored synthetic baseline fixtures for known variants.

Exit: architecture prevents cross-parser helper imports, and unsafe fixture
examples fail locally and in CI.

Commit: `refactor(parsing): isolate portal parser pipelines`

### Slice 7 — Immutable releases, activation, and rollback

First failing test:
`tests/integration/test_parser_activation_postgres.py::test_activation_rejects_missing_worker_capability`.

- Implement content-addressed release records and per-portal active pointers.
- Record Git/build/config/dependency provenance and worker-supported hashes.
- Add activation epoch fencing, `retired` versus `revoked`, manual activation,
  rollback, and effective non-revoked read selection.
- Require candidate and previous releases on healthy NAS and VPS workers.

Exit: atomic concurrency tests prove one active pointer, late-result rejection,
and portal-isolated rollback.

Commit: `feat(parsing): add audited parser release activation`

### Slice 8 — Offline benchmark lane

First failing test:
`tests/unit/test_benchmark_isolation.py::test_benchmark_worker_cannot_construct_network_transport`.

- Implement immutable manifests, deterministic stratified selection, fixture
  inclusion, 1,000-raw-entry ceiling, progress, and unavailable-input results.
- Run active and candidate against identical bytes on one NAS-only worker.
- Add raw-variant gates, diff clustering, review states, eligibility evaluation,
  output expiry with artifacts, and short-lived manifest-scoped identity.
- Add resource limits and the NAS Compose service.

Exit: a synthetic active/candidate comparison exercises correct, incorrect,
ambiguous, unreviewed, expired, and changed-build behavior without network
capability or production writes.

Commit: `feat(parsing): benchmark candidates offline on NAS`

### Slice 9 — Maintenance REST API, thin CLI, and dashboard

First failing test:
`tests/unit/test_parser_maintenance_auth.py::test_raw_read_requires_exact_artifact_scope_and_purpose`.

- Add paginated safe cluster/metadata endpoints and exact one-artifact streaming;
  no raw list, wildcard, directory, or bulk endpoint.
- Add forced-command SSH token issuer contract and 30-minute scoped bearer tokens.
- Add `homez-artifacts` metadata/raw-stream CLI with no cache, fixture generation,
  fact writes, or activation rights.
- Extend the current dashboard with Live errors, Parser quality, and Parser
  versions; preserve SSE/load-more, add two-version comparisons and audited raw
  escaped-text view.

Exit: role, CSRF, token scope/expiry, pagination, cache header, XSS, and raw-body
logging tests pass.

Commit: `feat(operations): add parser maintenance and quality views`

### Slice 10 — Recovery and backlog migration tooling

First failing test:
`tests/integration/test_parser_recovery_postgres.py::test_activation_replays_only_newest_capture_without_fetch`.

- Automatically enqueue bounded lower-priority artifact-backed recovery after
  activation.
- Add manual network-recovery release in 50/150/remainder batches.
- Transactionally supersede legacy retry/dead-letter jobs and enqueue one current
  task per listing; retain old attempts as audit.
- Respect inactive/stale distinctions, source cooldown, redirect handoff, and
  release/epoch idempotency.

Exit: production-like fixtures simulate the 968-job shape without historical
downloads, same-version parser loops, priority inversion, or a real backlog
release. The operator command defaults to dry-run and requires an explicit batch.

Commit: `feat(scraping): recover newest listings after activation`

### Slice 11 — Production retention, backup warnings, and alerts

First failing test:
`tests/integration/test_production_retention_postgres.py::test_replay_does_not_extend_capture_retention`.

- Implement daily bounded two-year purge by page-capture `fetched_at` and
  indefinite minimal listing tombstones.
- Expire detailed benchmark output with raw artifact deletion.
- Add database-size measurement, 10 GB boundary state, seven-day re-arm, retention
  failure/reminder/recovery mail, and safe dashboard state.
- Add backup manifest dates and non-blocking inspection/restore warning; keep
  backup pruning manual and raw artifacts excluded.

Exit: frozen-clock tests cover independent capture expiry, replay, newer fetch,
manual backup retention, size oscillation, failed measurement, purge backlog,
and recovery notification.

Commit: `feat(operations): enforce parser data retention`

### Slice 12 — Gratka vertical parser release

First failing test:
`tests/unit/parsers/gratka/test_variants.py::test_reviewed_gratka_fixture_extracts_core_contract`.

- After separate deployment authorization, capture the 25-newest production
  discovery canary with the active baseline. Until then, use only existing safe
  fixtures and record the raw-evidence gate as pending.
- Review artifacts through the guarded maintenance path; manually author and scan
  synthetic fixtures.
- Implement Gratka variants and candidate release only in its package.
- Run the offline benchmark, review every new diff signature, and present manual
  activation evidence.

Exit: activation remains a human action; no other portal parser behavior changes.

Commit: `feat(gratka): improve versioned page extraction`

### Slices 13–15 — Morizon, Otodom, and OLX releases

Repeat Slice 12 independently in this order:

1. Morizon — `feat(morizon): improve versioned page extraction`
2. Otodom — `feat(otodom): improve versioned page extraction`
3. OLX — `feat(olx): improve versioned page extraction`

Copy useful starting logic within the destination package; do not replace it
with shared parsing abstractions. Each slice has its own discovery artifacts,
fixtures, variants, benchmark, reviews, activation evidence, and rollback target.
Code and synthetic-fixture work may proceed without deployment; raw benchmark
evidence and activation remain explicit production gates.

### Slice 16 — Cutover and legacy removal

First failing test:
`tests/architecture/test_deployment_topology.py::test_no_primary_fallback_scraper_configuration_remains`.

- Complete Gratka 25/50/150/remainder rollout evidence, then repeat by approved
  portal order.
- Remove `RemotePortalScraper` fallback configuration and per-process limiter
  state only after queued workers are healthy and legacy jobs are superseded.
- Update `docs/nas-scrapers.md`, `docs/deployment.md`, `docs/operations.md`, env
  examples, release checklist, and topology tests.
- Run denial, NAS outage, VPS outage, artifact outage, rollback, and restore drills.

Exit: both deployments concurrently consume the central queue, no legacy
fallback is reachable, all observability is live, and activation remains manual.

Commit: `feat(scraping): complete concurrent worker cutover`

## Security review gate

Before any production activation, verify:

- worker and maintenance identities are distinct, least-privilege, source/run
  scoped, short-lived where designed, and enforced server-side;
- coordinator, artifact, and dashboard routes are private and not exposed by
  public Caddy paths;
- redirects and listing URLs remain source allowlisted and SSRF-safe;
- proxy and portal credentials exist only in secret files, never task payloads,
  logs, database rows, Compose interpolation, images, or benchmark manifests;
- untrusted HTML/JSON is bounded, never executed, never rendered unescaped, and
  cannot initiate network access during parsing or benchmarking;
- artifact upload/read validates size, identifier, manifest membership, content
  hash, authorization, and audit purpose;
- per-object encryption keys and raw bytes are absent from backups and VPS disk;
- activation, rollback, raw read, manual deletion, network-recovery release, and
  retention actions have CSRF/auth/audit coverage;
- concurrency tests cover duplicate claims, late results, epoch changes, budget
  reservation races, deletion/read races, and alert deduplication;
- dependency and image scans pass after any new proxy/HTML/crypto dependency.

## Verification commands

Run targeted tests during each slice, then before every slice commit run the
applicable subset and finish the epic with:

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest
.venv/bin/pip-audit --requirement requirements.lock --no-deps --disable-pip
.venv/bin/yamllint -d relaxed .github/workflows .github/dependabot.yml
DATABASE_URL=sqlite:///migration-check.sqlite3 .venv/bin/alembic upgrade head
docker compose --env-file .env.example -f infra/compose.yaml config --quiet
```

Also run PostgreSQL-marked integration tests against PostGIS, build the immutable
image, validate every NAS/VPS Compose combination, and run the fixture-safety
scanner over all committed email, HTML, and JSON fixtures. Record anything that
cannot run locally rather than treating it as passed.

## Deployment gates and operator inputs

Repository implementation can proceed with fakes, but production cutover requires:

- reviewed per-portal source interval and attempt/success ceilings;
- Webshare credential secret, billing-cycle anchor, and verified free allowance;
- NAS artifact path, NAS-only key-encryption key, service identities, and
  Tailscale grants;
- operational alert recipient and successful test notification;
- immutable image digest deployed to both worker pools with matching capability
  heartbeats;
- database backup and restore evidence plus displayed retention warning;
- explicit manual activation per portal after benchmark review.

Do not unblock one missing portal by coupling its parser or rollout to another.
Do not deploy, enter secrets, release network recovery, or activate a parser as
an implicit consequence of merging code.
