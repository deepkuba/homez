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

Status: repository implementation complete (2026-09-09); deployment remains gated.

Evidence:
- Added the named PostgreSQL distinct-claims test first. Its initial run skipped
  because no test database was configured. The fake-backed unit contract then
  failed on missing queue models before production implementation.
- Provisioned a disposable PostgreSQL 14.24/PostGIS 3.2 test instance entirely
  under `/tmp`, from extracted distribution packages, using a private Unix
  socket and no TCP listener, root changes, Docker, or production credentials.
  The named test subsequently passed against real concurrent transactions.
- Added source/task-class enqueue, idempotency, capability registration, claims,
  bounded lease renewal, defer/succeed/fail, expiry audit, retry limits, held
  network recovery, and stable source-scoped status pagination.
- Migration `20260909_23` is expand-only. It adds an empty current-release/epoch
  pointer now because queue fencing requires it; Slice 7 still owns audited
  activation/rollback and seeds no release in this slice.
- Private worker API is disabled by default, binds each credential digest to one
  source/deployment/worker, bounds request bodies, rejects arbitrary raw fields,
  and exposes neither enqueue nor activation/recovery-release operations.
  Production worker secret provisioning and private Tailscale routing remain
  deployment gates. Workers receive no database credentials.
- Test-first review regressions fixed duplicate live enqueue across epoch changes,
  acknowledgement using a stale pre-lock timestamp, and malformed cursor types.
- Five PostgreSQL tests cover distinct NAS/VPS claims, concurrent enqueue,
  simultaneous acknowledgements, expiry/epoch fencing, and delayed lock handling.
  SQLite covers deterministic state transitions and migration expansion; it is
  not used as evidence of concurrent-claim safety.
- Final full suite: **280 passed, zero skipped**, with PostgreSQL required.
  PostgreSQL and SQLite Alembic upgrade/schema-drift checks pass.
  Ruff formatting/lint, strict mypy, fixture scanner, YAML lint, and dependency
  audit pass. All five base/shared-VPS/scraping/NAS Compose combinations validate
  with Compose v1.29.2. Docker v2/image build remains unavailable due daemon
  permissions; CI must still build the immutable image and verify PostGIS 17.
- Security/diff review found no secrets, raw source captures, unsafe fixtures,
  debug code, public-ingress changes, or report modifications. Synthetic catalog
  values and fake credential digests exist only in tests. Original user changes
  to the main implementation plan, glossary, ADR, and prompt remain preserved.
- Reassessment for Slice 2: reuse the source-pinned API contracts; add worker
  shutdown/heartbeat and queued workflow dispatch behind the disabled flag.
  Do not enable network work until Slice 3 central pacing is installed.
  Route allocation/accounting is intentionally unassigned in Slice 1 and belongs
  to Slice 3; metadata-retention configuration is validated but purge remains a
  later operations implementation. All live rollout gates remain closed.


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

Status: repository implementation complete (2026-09-10); runtime ships dark.

Evidence:
- Named PostgreSQL worker test failed on missing typed result/runtime before
  production edits. Unit tests failed first for lease heartbeats, shutdown,
  unknown release/source rejection, network-disabled transport, response-only
  parsing, queued normalization, capture handoff, and private coordinator client.
- Healthy NAS and VPS fake transports concurrently complete different PostgreSQL
  tasks with no duplicate request or result. Worker/API roundtrip proves raw
  bytes never enter completion payloads or PostgreSQL.
- Worker execution is source/release-pinned with separate lease heartbeat,
  bounded in-flight shutdown, configured-identity verification, and short-lease
  rejection. No worker imports catalog ORM or receives database credentials.
- Added injected bounded HTTP response transport: 18 fake tests cover exact
  decompressed bytes, 2 MB transfer/input limits, deadlines, response closure,
  gzip/deflate bombs, truncated/concatenated streams, and no followed redirects.
  Packaged worker remains empty-capability/network-disabled until later gates.
- Normalization now enqueues/awaits configured sources behind the existing off
  flag, releases workflow leases while pending, and resumes partial facts through
  unchanged downstream/report code. Legacy fallback remains default.
- Migration `20260909_24` adds production-only parser result and field candidate
  tables to provide the durable handoff required by Slice 2. Slice 5 expands
  field-resolution/provenance policy. Capture metadata/results/ack commit
  atomically, duplicate identical acknowledgements are idempotent, stale
  epochs/task classes are fenced, and expiry is anchored to capture fetched_at.
  Benchmark models/repositories remain physically separate and unimplemented.
- Parser errors complete explicit unknowns without refetch; missing fields record
  `artifact-unavailable` until Slice 4. Lost acknowledgements retry the same
  metadata three times. Total worker/coordinator loss still has an ambiguous
  external HTTP outcome; lease recovery may refetch. Healthy concurrency is
  proven, not an exactly-once external-network guarantee.
- User-requested Astra low subagent implemented Compose/topology and bounded
  transport tests, then independently reviewed runtime/result safety. Review
  regressions closed traceback leakage, expiry reads, task-class forgery,
  acknowledgement retries, and safe coordinator failure responses.
- Full suite: **329 passed, zero skipped**, PostgreSQL required. Final additional
  API regression: **11 passed**. Subsequent CI/topology check passes separately.
  Ruff format/lint, strict mypy, fixture scanner, YAML lint, dependency audit,
  SQLite migration tests, PostgreSQL upgrade and Alembic drift checks pass.
- NAS standalone, base+VPS, and full shared/legacy+VPS concurrent Compose profiles
  validate with synthetic coordinates/digest using Compose v1. CI now validates
  both new models with Compose v2. Image build/PostGIS 17 remain CI gates; local
  PostgreSQL 14.24/PostGIS 3.2 was disposable under /tmp with a private Unix socket.
- Diff/security review: no real portal/Webshare calls, raw fixtures, credentials,
  debug output, report presentation changes, or unrelated user edits.
- Reassessment: Slice 3 must supply aggregate source pacing, denial handling,
  route allocation and proxy byte accounting before network transport can run.
  Slices 4–7 must supply artifacts and reviewed executable releases before live
  activation. No deploy, live parser activation, backlog, or recovery release.


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

Status: repository implementation complete (2026-09-10); routing remains dark.

Evidence:
- The named PostgreSQL test was added first and initially skipped without a
  configured test database; the focused fake-backed contract then failed on the
  absent source-budget module. The final named test uses two concurrent worker
  transactions and proves one aggregate portal start plus a locked proxy-byte
  reservation at the 900 MB ceiling.
- Central source rows serialize the minimum interval, daily attempt/success
  counts, and monotonic cooldown. Proxy allocation uses opaque healthy route
  identifiers, source-specific quarantine, compressed-byte reservations, a
  monthly ledger, and direct continuation after the 900 MB usable allowance.
- Deterministic denial tests cover positively identified proxy infrastructure
  failure, ambiguous response handling, a single direct fallback, the 15-minute
  proxy-denial delay, longer `Retry-After`, and direct-denial cooldowns of 6, 12,
  and 24 hours. A deferred direct fallback releases its worker lease and is
  consumed once by a later fenced queue attempt under normal source pacing.
- Validated cross-source listing redirects complete the source attempt and
  create one durable, idempotent target-portal handoff. The handoff contains no
  response bytes and cannot invoke either portal parser.
- Virtual-clock evidence covers all four portals: 1,000 successful responses fit
  in one day at one aggregate start per 10 seconds, and the 43rd completion in a
  45-task burst occurs at 430 seconds. A 15-second profile is explicitly marked
  unavailable rather than dropping work.
- Focused unit suite: **71 passed**. Full non-PostgreSQL suite: **389 passed**;
  full PostgreSQL suite: **14 passed** against a fresh disposable PostgreSQL
  14.24 database on a private Unix socket. Migration downgrade/upgrade and
  Alembic drift checks pass on PostgreSQL; SQLite migration regression tests pass.
- Ruff format/lint, strict mypy, architecture tests, dependency audit, and the
  NAS/VPS concurrent Compose profiles pass with synthetic coordinates and an
  immutable fake digest. Compose v2 and immutable-image builds remain CI gates.
- Diff/security review found no credentials, endpoint details, raw source
  content, portal/Webshare requests, unsafe fixtures, debug code, report changes,
  or production activation. Existing user edits outside this plan remain
  unstaged. Slice 4 may add NAS-only encrypted diagnostics; no network worker is
  enabled until all later rollout gates are satisfied.

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

Status: repository implementation complete (2026-09-10); NAS rollout is gated.

Evidence:
- The named integration test failed first on the absent worker artifact-writer
  injection before production code changed. It now proves one in-memory upload
  of exact non-text parser bytes, one portal fetch, partial fact preservation,
  no VPS file write, and no raw bytes in coordinator metadata or logs.
- The NAS-local store encrypts each object with a fresh AES-GCM data key and
  wraps that key with a NAS-only KEK. Transactional `(source, content_hash)`
  deduplication, the 2 MB bound, tamper rejection, earliest 30-day expiry,
  immediate expired-read refusal, hourly crypto-shred, retryable idempotent
  deletion, and safe tombstones have deterministic filesystem tests.
- A structurally diverse quality reservoir retains at most five unreviewed
  positive samples per portal variant. Correct review shreds a positive-only
  sample; incorrect review promotes it to a diagnostic; an existing diagnostic
  always wins deduplication and cannot be removed by positive review.
- The private API source-pins worker uploads and restricts maintenance and
  benchmark reads to exact identifiers, with frozen benchmark membership. It
  has no list/bulk route, enforces actual streamed size, records every attempted
  raw read before release, fails closed when audit persistence fails, suppresses
  access logs, and sets `Cache-Control: no-store`.
- Central PostgreSQL stores only an opaque artifact identifier, availability,
  complete `missing_fields`, and expiry in a separate diagnostic-run row.
  Artifact outage records `artifact-unavailable`, keeps partial facts, performs
  no refetch, and creates no VPS fallback file. Object paths, ciphertext, wrapped
  keys, raw bytes, and the KEK never enter the application database.
- The standalone NAS Compose model is opt-in, resource limited, tailnet-bound,
  and uses separate data, audit, and secret paths absent from backup mounts.
  Operations docs require those paths to be excluded from NAS snapshots,
  replication, and recursive backup roots.
- Focused artifact and handoff suite: **70 passed**. Full non-PostgreSQL suite:
  **445 passed**; full PostgreSQL suite: **14 passed** against a fresh disposable
  database. PostgreSQL migration downgrade/upgrade and Alembic drift checks,
  SQLite migration tests, Ruff format/lint, strict mypy, architecture tests,
  YAML lint, and dependency audit pass.
- Modern Compose rendering, real NAS paths, the base64 32-byte KEK, expiring
  scoped identities, private network grants, immutable image build, and live
  caller wiring remain deployment gates. Installed Compose v1 cannot parse the
  fail-closed `bind.create_host_path: false`; CI owns the Compose v2 render.
  No deployment, secret access, production fetch, parser activation, or report
  behavior change occurred.

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

Status: repository implementation complete (2026-09-10); parser rollout is gated.

Evidence:
- The named PostgreSQL test was added first and failed on the absent resolved
  production-field model. It now proves that a partial result retains all
  normalized candidates, semantic origins, structural locators, parser release,
  explicit value/unknown/ambiguous outcomes, and its diagnostic linkage.
- Production results, field candidates, and resolved outcomes are immutable rows
  linked to the page capture. Equal-authority disagreement stays ambiguous;
  summary and attribute evidence outrank description, and all page evidence
  outranks email fallback without deleting the lower-priority candidate.
- Resolution declares the eleven core extraction fields plus the separate
  `price_per_sqm_minor` derived fact. Explicit page price-per-square-metre wins;
  a page price/area fallback rounds once to minor units, and differences above
  100 PLN/m² produce a consistency signal while retaining the explicit value.
- Configurable low/normal/high bands validate strictly ordered currency-specific
  boundaries; missing facts remain `unknown`. No market boundary is hard-coded.
  Candidate fact sets include a newly available price-per-square-metre value,
  while existing payloads and report serialization remain unchanged when it is
  absent. Partial facts continue through the existing workflow.
- Focused parser/result/workflow suite: **31 passed**. Full non-PostgreSQL suite:
  **447 passed**; full PostgreSQL suite: **15 passed**. PostgreSQL migration
  downgrade/upgrade and Alembic drift checks, Ruff format/lint, strict mypy,
  architecture tests, fixture scanning, and dependency audit pass.
- Diff review found no raw source content, external requests, credentials,
  unsafe fixtures, debug code, activation controls, or report presentation,
  ranking, scoring, section, or delivery changes. Parser implementation and
  rollout remain gated on Slices 6–7.

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

Status: repository implementation complete (2026-09-10); releases remain inactive.

Evidence:
- The named architecture test was added first and failed because all four
  source parser packages lacked independent parser modules. It now follows each
  implementation's imports and permits only the shared typed result contract;
  no parser imports another parser, a shared extraction helper, transport, or
  persistence code.
- Gratka, Morizon, Otodom, and OLX each own their complete bounded extraction
  implementation. Positive baseline detection selects exactly one handler;
  absent, malformed, or multiply matching evidence returns `unknown-variant`
  with all eleven core fields explicitly missing and no invented candidates.
- Each known baseline test uses a manually authored, minimal synthetic HTML
  fixture and verifies normalized facts, candidate provenance, parser release,
  and derived price per square metre. No fixture was generated from captured
  source content and no parser performs a network request or browser fallback.
- The fail-closed fixture scanner now discovers `.eml`, `.html`, and `.json`,
  bounds inputs to 2 MiB, requires strict UTF-8 and complete parsing, and rejects
  active URLs, contacts, secrets, executable markup, duplicate JSON keys, and
  malformed or trailing content. CI and local development use the same command.
- Focused parser, architecture, and scanner suite: **29 passed**. Full
  non-PostgreSQL suite: **469 passed**; full PostgreSQL suite: **15 passed**.
  Ruff format/lint, strict mypy, fixture scanning, YAML lint, dependency audit,
  and existing NAS/VPS Compose models pass.
- Diff review found no real portal content or calls, credentials, unsafe fixture
  data, debug code, cross-parser helper reuse, report changes, activation, or
  deployment action. Slice 7 must still build immutable releases and enforce
  audited portal-specific activation before any parser can consume live work.

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

Status: repository implementation complete (2026-09-10); activation remains gated.

Evidence:
- Added the named PostgreSQL capability-gate test first. Its initial run skipped
  because no disposable database was configured; after provisioning an isolated
  PostgreSQL 14/PostGIS 3.2 instance under `/tmp`, it passed without TCP access
  or production credentials.
- Release identities are SHA-256 addresses of portal, parser bytes,
  portal-specific configuration, and dependency lock content. Immutable records
  retain the human version, Git commit, deployable digest, creation time, and a
  qualifying benchmark reference; conflicting provenance cannot reuse a hash.
- Manual activation locks a stable portal row and its pointer, checks the
  expected epoch, and requires fresh healthy NAS and VPS workers to advertise
  both candidate and rollback hashes. Each successful portal-only pointer change
  increments the epoch and writes bounded actor, comparison, and time evidence.
- Normal promotion retires the prior release. Manual rollback restores the
  recorded prior hash without a benchmark and revokes the withdrawn release;
  immutable result history is unchanged. Effective reads accept audited retired
  history, exclude revoked releases, and require the result's release/epoch to
  have been active.
- Focused fake-backed activation, rollback, epoch, queue, and effective-read
  suite: **18 passed**. PostgreSQL capability and simultaneous-activation tests:
  **2 passed**. Full non-PostgreSQL suite: **473 passed**; full PostgreSQL suite:
  **17 passed**. PostgreSQL and SQLite migration upgrade/downgrade checks and
  PostgreSQL schema drift pass.
- Ruff format/lint, strict mypy, fixture scanning, YAML lint, dependency audit,
  and existing base/NAS/VPS Compose models pass. Diff review found no secrets,
  raw source content, external calls, activation command, release seed, report
  change, or production deployment.
- The qualifying benchmark identifier is a fake-backed, fail-closed contract in
  this slice. Slice 8 must persist and evaluate real offline benchmark evidence,
  and Slice 9 must expose authorized manual maintenance controls. Until both
  exist and a separately authorized deployment supplies immutable images and
  worker capability heartbeats, no production parser release can be activated.

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

Status: safe repository contract complete (2026-09-10); NAS runtime deployment gated.

Evidence:
- Added the named isolation test first and confirmed its expected failure because
  no benchmark worker existed. The worker now accepts only an injected bounded
  input reader and two parser callables; the transitive architecture test proves
  the benchmark package cannot import portal/network clients, production queue,
  workflow, or catalog persistence.
- Immutable manifests include all supplied synthetic fixture versions and use a
  deterministic stratified round-robin sample of at most 1,000 deduplicated raw
  artifacts. They record content hashes, active/candidate release hashes,
  selection policy, and eligible/selected counts per variant/failure stratum.
- Active and candidate parsers receive the identical `PageInput` bytes in
  memory. Unreadable entries remain `benchmark-input-unavailable`, progress
  reports processed/total/portal/variant/unavailable counts, and no input is
  replaced or fetched from a portal.
- Benchmark manifests, runs, detailed results, field candidates, and difference
  reviews have dedicated tables and repository paths, physically separate from
  production results and candidates. Detailed raw-derived values are encrypted
  with a required 256-bit key and inherit artifact expiry; artifact deletion
  erases ciphertext and candidates while retaining safe aggregate tombstones.
- Activation now validates a persisted complete eligible run for the exact
  portal and candidate hash. Eligibility requires live raw coverage for every
  changed variant and blocks unavailable/expired inputs, incorrect or unreviewed
  differences, and ambiguous differences that add a value. A changed build hash
  cannot inherit another candidate's run.
- Benchmark artifact reads now require both frozen-manifest membership and the
  short-lived identity's exact artifact subset. Audit-before-read, no-cache
  behavior, and in-memory response handling remain enforced.
- Focused benchmark, artifact-auth, activation, and architecture suite:
  **32 passed**. Full non-PostgreSQL suite: **481 passed**. Ruff format/lint,
  strict mypy, fixture scanning, YAML lint, dependency audit, SQLite migration
  upgrade/downgrade and schema drift, and existing Compose models pass.
- A single-worker resource contract fixes one instance, 0.5 CPU, 256 MiB memory,
  one database connection, low I/O weight, and positive nice priority. The NAS
  Compose service remains a deployment gate because no authorized immutable
  active/candidate image pair or short-lived manifest identity issuer exists.
  Do not add a nonfunctional service or give it portal/proxy credentials; add
  the runnable service only with those production inputs and explicit deployment
  authorization. PostgreSQL migration/concurrency checks remain required in CI
  because the disposable local PostGIS installation was unavailable on this run.

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

Status: repository implementation complete (2026-09-10); deployment remains gated.

Evidence:
- Added the named exact-scope test first and confirmed the maintenance module was
  missing. Raw grants now bind one subject, artifact identifier, approved review
  purpose, and timezone-aware expiry; mismatched identifiers/purposes and the
  exact 30-minute boundary fail closed.
- A forced-command issuer accepts only `homez-artifacts issue-token`, signs the
  bounded grant, and rejects tampering. The private API exposes bounded paginated
  safe metadata/cluster endpoints and exactly one artifact text stream. It has no
  raw listing, wildcard, directory, bulk, fixture-generation, fact-write, parser
  activation, or recovery-release operation.
- Raw streaming audits before reading, returns `no-store` and `nosniff`, never
  logs response bytes, and renders as escaped/plain text rather than executable
  markup. Authorization errors do not expose token or raw values.
- The `homez-artifacts` entry point supports only metadata and one exact raw
  stream, requires HTTPS and a token file, requests `no-store`, streams bounded
  chunks without a cache, and has no production mutation command.
- The existing private scraper dashboard retains its live SSE and load-more
  behavior and adds Live errors, Parser quality, and Parser versions views. Safe
  database values are escaped and the versions view states the two-version
  active/candidate comparison boundary; no report presentation changed.
- Focused authorization, API, CLI, XSS, SSE, pagination, and dashboard suite:
  **8 passed**. Strict mypy and focused Ruff checks pass. Production SSH forced-
  command installation, signing-key secret, artifact-service wiring, audit sink,
  and authorized operator identities remain deployment gates; no token was
  issued and no raw artifact was read in this slice.

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

Status: repository implementation complete (2026-09-10); releases remain gated.

Evidence:
- Added the named PostgreSQL newest-capture test first; it skipped because no
  disposable PostgreSQL URL was configured. A fake-backed vertical test then
  failed on the missing recovery repository before implementation.
- Activation now invokes bounded artifact-recovery planning after its audited
  pointer change. Planning selects only the newest capture of the newest
  snapshot per non-inactive listing, requires a stored unexpired artifact and a
  revoked or incomplete prior result, and deduplicates by portal, content hash,
  and release hash so epoch changes cannot cause same-version replay loops.
- Artifact recovery has a distinct queue binding and completion path. It exposes
  the exact retained artifact/capture identity, creates no page capture or portal
  request, preserves original `fetched_at`-derived result expiry, writes a new
  immutable production result, and reuses the diagnostic artifact when fields
  remain missing. Live work retains priority 0 over artifact priority 10 and
  network-recovery priority 20.
- Manual network recovery remains held by default. The operator command defaults
  to dry-run, requires an explicit portal and `50`, `150`, or `remainder` batch,
  and requires both `--execute` and a bounded actor to mutate state. A locked
  campaign enforces the sequence exactly once, records each release, and pauses
  for source cooldown, portal denial, a material new variant, or a confirmed
  production regression. No command was executed against a real backlog.
- Transactional legacy migration locks retry/dead-letter normalization jobs,
  retains their attempt rows, marks them superseded, clears stale leases, and
  creates one held task for each active listing's newest snapshot and active
  release. Explicit inactive lifecycle evidence suppresses recovery; `stale`
  remains distinct and eligible.
- Focused recovery, queue, activation, capture-handoff, and normalization suite:
  **28 passed**, with the named PostgreSQL test skipped locally. A synthetic
  968-task backlog proves dry-run and 50/150/remainder release behavior without
  any fetch. Ruff format/lint, strict mypy, and SQLite migration upgrade,
  downgrade, and schema-drift checks pass.
- PostgreSQL race tests remain a CI gate and the production backlog release is an
  explicit authority boundary. Deployment must also wire the NAS artifact reader
  to the artifact replay input; neither network recovery nor parser activation
  is authorized by this repository change.
- Added a fake-backed, network-free artifact recovery worker prerequisite on
  2026-09-14. It accepts only artifact-recovery leases, verifies retained bytes
  against the capture hash and 2 MB bound, runs only the lease-pinned parser,
  and submits through the separate replay completion path. Focused worker and
  recovery tests: **5 passed**; Ruff and strict mypy pass. Production wiring
  remains gated on a NAS-local reader with an exact, short-lived recovery
  identity and matching coordinator endpoints; no broad raw read or portal
  transport was added.
- Full repository verification after the worker contract: **525 passed**, with
  19 PostgreSQL tests skipped because `TEST_POSTGRES_URL` is unavailable.
- Added the scoped recovery control plane and raw-read boundary on 2026-09-17.
  Generic live/network claims now reject artifact tasks; only a NAS deployment
  identity can claim, inspect, or complete artifact replay. The HTTP client
  exchanges typed metadata/results only. A separate exact-ID recovery artifact
  identity can read one bounded object, with audit and expiry enforced by the
  existing private artifact service. Focused coordinator, artifact, worker, and
  recovery tests: **48 passed**.
- Full repository verification after recovery endpoint wiring: **529 passed**,
  with 19 PostgreSQL tests skipped because `TEST_POSTGRES_URL` is unavailable;
  Ruff formatting/lint and strict mypy pass. Deployment still requires the
  authorized issuer/configuration to mint the exact short-lived artifact scope
  and a NAS recovery process. No credential was minted and no replay ran.
- The selected recovery authorization uses an Ed25519 coordinator capability,
  replacing the interim static recovery credential. The signed claims bind the
  exact artifact, portal, NAS worker, task, activation epoch, hashed lease token,
  recovery audience, issue time, and expiry. Expiry is capped by both 30 minutes
  and the shorter queue lease; cross-artifact reuse, tampering, wrong audience,
  non-NAS issuance, and boundary expiry fail closed.
- The VPS coordinator alone mounts the private signing key. The NAS artifact
  service mounts only the public verification key, accepts the capability for
  one audited bounded read, and keeps static recovery roles disabled. Raw bytes
  and the signing key never enter a task, queue row, log, worker image, or NAS
  artifact-service configuration. Focused capability, API, client, worker,
  service-config, configuration, and topology tests: **77 passed**.
- Full repository verification after signed-capability wiring: **532 passed**,
  with 19 PostgreSQL tests skipped because `TEST_POSTGRES_URL` is unavailable.
  Ruff formatting/lint, strict mypy, and the dark NAS/VPS worker Compose models
  pass locally. Installed Compose v1 cannot validate the artifact overlay's
  existing `create_host_path` option, so Compose v2 remains its CI gate. No key
  was generated or installed, no capability was issued outside synthetic tests,
  and no recovery process or live replay ran.
- Added the runnable NAS artifact-recovery processes after a focused worker test
  first failed on missing release registration and lease renewal. Each portal
  has a separate source-pinned process with its own coordinator identity, 0.25
  CPU, 128 MiB memory, no proxy configuration, no static artifact credential,
  and no portal transport. The process advertises only its immutable packaged
  release, renews its lease while reading/parsing, verifies the 2 MB content
  hash, and submits through the fenced replay endpoint. Focused worker and
  topology tests: **4 passed**. Deployment identities and starting the opt-in
  profile remain operator gates; no recovery task was claimed.

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

Status: repository implementation complete (2026-09-10); daily production scheduling and notification delivery remain deployment gates.

Evidence:
- Added the named PostgreSQL replay-retention test first; it skipped because no
  disposable PostgreSQL URL was configured. The frozen-clock repository test
  then failed on the absent retention module before implementation.
- A bounded daily repository deletes production details in oldest-capture order
  using the capture's `fetched_at`, never replay time. Newer captures remain
  independent, and a content-free listing tombstone is created only after the
  listing's final detailed parser result is removed. Explicit inactive evidence
  is retained separately from stale eligibility.
- Expired benchmark values and field candidates are erased independently from
  production models while retaining only portal, variant, outcome, artifact
  reference, and deletion time in a dedicated safe tombstone.
- Each run persists its cutoff, bounded deletion and backlog counts, oldest
  remaining capture, duration, health, and complete database size before and
  after retention. PostgreSQL measurement uses decimal 10 GB boundaries and
  bounded largest-relation metadata; SQLite supplies deterministic local tests.
- Size notification state is independent per 10 GB boundary, resets its
  below-boundary streak on a failed measurement, and re-arms only after seven
  consecutive successful daily measurements below that boundary. Retention
  failures alert immediately, remind no more than once per 24 hours, and emit
  one recovery notification. Alert payloads contain bounded operational data
  and no listing content.
- Backups now receive a mode-0600 sidecar manifest with creation time, oldest
  included production capture time, schema version, and an explicit raw-artifact
  exclusion. Inspection and restore show the current two-year cutoff as a
  non-blocking warning. Backup pruning remains an explicit operator command.
- Focused retention, backup, benchmark, and recovery suite: **17 passed**, with
  the named PostgreSQL test skipped locally. Full non-PostgreSQL suite: **495
  passed**. Ruff format/lint, strict mypy, and SQLite migration upgrade,
  downgrade, and schema-drift checks pass.
- Running `run-parser-retention` or scheduling it daily, delivering a successful
  test notification, and PostgreSQL migration/concurrency verification remain
  deployment gates. No mail, backup, restore, purge, or infrastructure action
  was run against production.

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

Status: safe synthetic implementation complete (2026-09-10); production evidence and activation remain gated.

Evidence:
- Added the named Gratka variant test first and confirmed the existing parser
  returned `unknown-variant` for the manually authored synthetic graph shape.
- Gratka alone now accepts exactly one explicitly typed `Apartment` or `House`
  from a top-level JSON-LD object, a top-level array, or a bounded `@graph`.
  It accepts a single-object offers array, retains structural provenance, and
  continues to reject multiple residences, unrelated nested recommendations,
  malformed JSON, invalid values, and responses above the shared 2 MB contract.
- The new fixture is minimal, synthetic, uses only a reserved domain, and passes
  the same fail-closed fixture scanner used in CI. No captured portal bytes,
  listing content, automatic fixture generation, browser, or network request
  entered the repository.
- The synthetic offline eligibility test proves the changed Gratka variant
  cannot qualify an immutable release without raw artifact coverage. The active
  parser pointer was not changed and no backlog or network recovery was released.
- Focused Gratka and architecture suite: **27 passed**. Full non-PostgreSQL
  suite: **497 passed**. Ruff format/lint, strict mypy, dependency audit, and
  fixture scanning pass.
- The separately authorized 25-newest discovery canary, guarded artifact review,
  persisted active/candidate benchmark, difference-signature review, immutable
  deployable image, and manual Gratka activation remain production gates.

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

Slice 13 status: safe synthetic Morizon implementation complete (2026-09-10);
production evidence and activation remain gated.

Slice 13 evidence:
- Added the focused Morizon variant test first and confirmed the baseline parser
  returned `unknown-variant` for the manually authored synthetic
  `WebPage.mainEntity` shape.
- The Morizon package alone now accepts one explicitly typed residence at its
  top level or in the bounded `mainEntity` position, retains source-local
  structural provenance, and keeps ambiguous or unrelated nested nodes unknown.
- The minimal reserved-domain fixture passes the fail-closed scanner. A
  synthetic-only eligibility test proves the changed variant cannot qualify
  without raw artifact coverage. No portal request, captured content, automatic
  fixture generation, browser, release registration, or activation occurred.
- Focused Morizon and architecture suite: **27 passed**. Full non-PostgreSQL
  suite: **499 passed**. Ruff format/lint, strict mypy, and fixture scanning pass.
- The separately authorized Morizon canary, guarded artifact review, persisted
  benchmark and signature decisions, immutable image, and manual activation
  remain production gates.

Slice 14 status: safe synthetic Otodom implementation complete (2026-09-10);
production evidence and activation remain gated.

Slice 14 evidence:
- Added the focused Otodom variant test first and confirmed the existing parser
  returned `unknown-variant` for the manually authored synthetic `listing-v2`
  shape.
- The Otodom package alone now recognizes its exact `listing-v1` and
  `listing-v2` markers. Version 2 reads one bounded `mainEntity` object while
  version 1 retains its existing top-level contract; malformed, mismatched, or
  duplicate markers remain unknown.
- The minimal reserved-domain fixture passes the fail-closed scanner, and its
  fixture-only benchmark result cannot satisfy changed-variant raw coverage.
  No portal request, captured content, generator, browser, release registration,
  or activation occurred.
- Focused Otodom and architecture suite: **24 passed**. Full non-PostgreSQL
  suite: **501 passed**. Ruff format/lint, strict mypy, and fixture scanning pass.
- The authorized Otodom discovery canary, artifact review, persisted benchmark,
  signature decisions, immutable image, and manual activation remain production
  gates.

Slice 15 status: safe synthetic OLX implementation complete (2026-09-10);
production evidence and activation remain gated.

Slice 15 evidence:
- Added the focused OLX variant test first and confirmed the existing parser
  returned `unknown-variant` for the manually authored synthetic `listing-v2`
  shape.
- The OLX package alone now recognizes exact `listing-v1` and `listing-v2`
  markers. Version 2 reads its single bounded `offer` object while version 1
  retains the existing top-level layout; malformed, mismatched, duplicate, and
  unrelated shapes remain unknown.
- The minimal reserved-domain fixture passes the fail-closed scanner, and the
  fixture-only benchmark result remains ineligible without raw changed-variant
  coverage. No portal request, captured content, generator, browser, release
  registration, or activation occurred.
- Focused OLX and architecture suite: **24 passed**. Full non-PostgreSQL suite:
  **503 passed**. Ruff format/lint, strict mypy, dependency audit, and fixture
  scanning pass.
- The authorized OLX canary, guarded artifact review, persisted benchmark,
  signature decisions, immutable image, and manual activation remain production
  gates.

Repeat Slice 12 independently in this order:

1. Morizon — `feat(morizon): improve versioned page extraction`
2. Otodom — `feat(otodom): improve versioned page extraction`
3. OLX — `feat(olx): improve versioned page extraction`

Copy useful starting logic within the destination package; do not replace it
with shared parsing abstractions. Each slice has its own discovery artifacts,
fixtures, variants, benchmark, reviews, activation evidence, and rollback target.
Code and synthetic-fixture work may proceed without deployment; raw benchmark
evidence and activation remain explicit production gates.

### Pre-cutover prerequisite — central network permits

Status: fake-backed repository contract complete (2026-09-10); production policy
and outcome-accounting wiring remain deployment gates.

Evidence:
- Added `test_worker_requires_central_network_permit_before_fetch` first and
  confirmed that the worker incorrectly fetched with no central reservation.
  The worker now requests a permit after claiming and before invoking transport;
  a coordinator error or denied permit produces no portal request, and only the
  granted opaque route identifier crosses into the transport contract.
- A denied permit is returned through the existing fenced deferral endpoint at
  the coordinator-selected `available_at`; it does not hold the lease until
  expiry or busy-wait in the worker.
- A successful permitted request is accounted through the private coordinator
  API before production task completion. The bounded transport carries both the
  exact post-decompression parser bytes and the compressed transferred-byte
  count, so proxy usage does not confuse artifact size with billed transfer.
- Typed bounded failures carry only response classification evidence, compressed
  byte count, and bounded retry delay. Proxy denial is centrally persisted and
  schedules its single delayed direct fallback; direct denial defers at the
  returned source cooldown. Unexpected transport failures are also accounted,
  without exception text, before the bounded task failure path. No failed or
  denied response reaches a parser or artifact writer.
- Added focused client and private API tests for the bounded
  `/internal/scrape/v1/network/reserve` round trip. The API authorizes the
  source-scoped worker and delegates to the transactional `SourceBudgetRepository`.
  With no configured budget repository it returns `503`, keeping the live path
  fail closed.
- Focused worker/client/API suite: **33 passed**. Ruff formatting/lint and strict
  mypy pass. No parser was packaged, no HTTP connector was enabled, and no live
  portal, proxy, secret, deployment, activation, or recovery action occurred.
- Full suite after success and failure accounting: **510 passed**, with 19
  PostgreSQL tests skipped because `TEST_POSTGRES_URL` is unavailable. Before
  deployment, load reviewed policies into the web coordinator, configure the
  Webshare billing anchor, and implement the reviewed connector that produces
  this typed evidence. The current worker remains intentionally unable to
  consume live work because `main()` advertises no releases and uses the
  disabled transport.
- Reviewed policy loading is now a bounded, strict four-portal file contract.
  Enabling the coordinator without the file, any portal, or a valid 1–28
  Webshare anchor day fails closed. The web app constructs the sole central
  budget repository from that file; workers do not receive it. Billing ledgers
  use the configured cycle-start date, backed by migration 32 expanding the key
  from `YYYY-MM` to `YYYY-MM-DD` without deleting rows.
- Policy/billing focused tests: **34 passed**. Full suite: **513 passed**, with
  19 PostgreSQL tests skipped because `TEST_POSTGRES_URL` remains unavailable.
  Ruff format/lint, strict mypy, SQLite upgrade/downgrade/re-upgrade, and
  Alembic schema drift checks pass. Production policy values and the actual
  Webshare billing anchor remain explicit operator inputs.

Commit: `feat(scraping): require central permits before worker fetches`

Follow-up commit: `fix(scraping): account denied network attempts`

Follow-up commit: `feat(scraping): load reviewed source budget policies`

### Pre-cutover prerequisite — immutable worker capability topology

Status: aggregate capability gate complete (2026-09-14); executable package
verification and deployment remain gated.

Evidence:
- Added `test_activation_accepts_aggregate_release_specific_workers` first and
  confirmed activation incorrectly required one process to claim both active
  and candidate code. Activation now unions healthy capabilities per deployment
  and still requires both candidate and rollback hashes independently on NAS
  and VPS.
- This permits one immutable release per worker process without weakening the
  portal, health-window, benchmark, manual activation, or epoch gates. A release
  available only on one deployment, or a required hash missing anywhere, still
  blocks activation.
- Focused release suite: **4 passed**, with two PostgreSQL tests skipped because
  `TEST_POSTGRES_URL` is unavailable. Ruff and strict mypy pass. No capability
  heartbeat, parser activation, image build, or deployment occurred.
- Added a package loader that derives the executable release hash from the
  source-owned parser files, the shared typed parser contract/configuration, and
  exact bounded dependency-lock bytes. It constructs the selected portal parser
  with that derived hash, and the result matches `ReleaseBuild.release_hash`;
  callers cannot relabel installed code with an arbitrary advertised hash.
- The runtime image now retains the build's `requirements.lock` at the fixed
  `/app/release/requirements.lock` path. Focused package, release, and
  architecture suite: **27 passed**; Ruff and strict mypy pass. Image building
  and wiring this verified parser together with the still-disabled production
  connector remain deployment prerequisites.

Commit: `fix(parsing): aggregate immutable worker capabilities`

Follow-up commit: `feat(parsing): derive packaged parser release identity`

### Pre-cutover prerequisite — bounded direct/proxy connector

Status: repository connector contract complete (2026-09-14); production worker
wiring and proxy inputs remain gated.

Evidence:
- Added `test_connector_resolves_only_granted_proxy_route_from_secret` first and
  confirmed no connector existed. The connector performs one HTTPS request,
  follows no redirects, and resolves only the coordinator-granted opaque route
  from a permission-checked local secret file. Proxy URLs and credentials are
  held as secret values and are absent from task/control payloads and errors.
- Unknown routes fail before connection. Positively identified proxy timeout,
  TLS, and tunnel-authentication failures produce typed pre-portal evidence;
  other failures remain ambiguous transport failures and cannot earn a proxy
  fallback. There is no second-proxy selection path.
- Denial responses are discarded without parsing while counting at most 2 MB of
  compressed bytes. Integer or HTTP-date `Retry-After` values are bounded and
  passed to the central decision, where a longer delay wins. Worker permit
  requests reserve the full 2 MB before proxy allocation; exhaustion continues
  through the existing direct route.
- Focused connector, transport, coordinator, budget, denial, and worker suite:
  **103 passed**. Ruff and strict mypy pass. The packaged worker entry point
  remains disabled until artifact handling is wired, so this commit cannot
  consume live tasks or contact a portal by itself.

Commit: `feat(scraping): add bounded direct and proxy connector`

### Pre-cutover prerequisite — runnable production worker boundary

Status: repository and Compose wiring complete (2026-09-14); deployment remains
gated on production inputs and explicit authorization.

Evidence:
- Added `test_artifact_client_streams_exact_bytes_with_source_scope` first and
  confirmed no remote artifact writer existed. VPS and NAS workers now stream
  the exact in-memory parser bytes once to the private NAS service using a
  source-scoped token; failures expose no bytes or credentials and preserve the
  existing `artifact-unavailable` partial-result path without refetching.
- The worker entry point composes the content-verified source parser, bounded
  direct/proxy connector, central permit/outcome client, and NAS artifact writer.
  Its immutable release-slot suffix permits active and candidate images to run
  concurrently with distinct identities; only a release matching queued active
  work can claim it.
- NAS/VPS Compose workers remain opt-in and read-only with existing CPU, memory,
  PID, capability, and health limits. Each receives only its coordinator token,
  source-scoped artifact token, and local proxy-pool secret. The VPS web overlay
  mounts the reviewed source-budget file and hashed coordinator identity
  registry; workers receive no database configuration.
- The reviewed policy carries only opaque proxy route IDs. The coordinator
  registers those IDs centrally, while addresses and credentials exist only in
  each worker's secret pool. Focused artifact/package/worker/topology tests:
  **21 passed**; both Compose models validate under local Compose v1. Ruff and
  strict mypy pass. No service was started and no secret, live request,
  activation, recovery release, or deployment occurred.
- Full repository verification after runtime wiring: **522 passed**, with 19
  PostgreSQL tests skipped because `TEST_POSTGRES_URL` is unavailable. YAML lint
  and both merged NAS/VPS Compose configurations pass locally.

Commit: `feat(scraping): wire immutable production worker runtime`

### Slice 16 — Cutover and legacy removal

Status: repository cutover drills in progress (2026-09-14); production rollout
and legacy removal remain gated.

Evidence:
- Added a deterministic NAS/VPS outage drill for both failure directions. It
  proves the healthy deployment claims distinct work while its peer is active,
  cannot duplicate the peer's live lease, reclaims the abandoned task after
  lease expiry, and fences completion by the stale owner.
- Focused queue and concurrent-worker verification: **13 passed**, with the
  PostgreSQL concurrency test skipped because `TEST_POSTGRES_URL` is not
  configured. The drill uses only synthetic catalog data and performs no portal
  or infrastructure request.
- Full repository verification: **524 passed**, with 19 PostgreSQL tests skipped
  for the same unavailable test database. Ruff formatting/lint, strict mypy,
  fixture safety, dependency audit, YAML lint, SQLite migration up/down/up, and
  the dark NAS/VPS Compose models pass locally; installed Compose v1 was used.
- Legacy fallback removal remains blocked by the required per-portal production
  rollout evidence. No fallback configuration was removed and no service was
  started, deployed, activated, or given recovery work.

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
