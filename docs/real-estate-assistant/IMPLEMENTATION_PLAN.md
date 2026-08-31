# Implementation Plan

- Status: In progress; Slices 0-4 implemented with synthetic fixtures
- Date: 2026-08-31
- Delivery mode: incremental implementation; no production integration is enabled

## 1. Product outcome

A private, single-buyer service that ingests permitted property alerts, maintains
a deduplicated Krakow-area listing history, applies transparent constraints and
preferences, and sends a Friday 10:00 report with up to 10 compliant and 10
exploration listings. It learns soft preferences from secure mobile feedback and
never silently changes hard rules.

## 2. Delivery boundary

### In scope

- Dedicated Gmail alert ingestion for approved sources.
- Source-specific parsing and, only where permitted, targeted listing retrieval.
- Normalized listings, snapshots, deduplication, and change detection.
- Versioned buyer profile, eligibility, explainable scoring, and diversified
  10+10 weekly selection.
- Google Routes multimodal commute enrichment with a hard free-quota guard.
- All-in acquisition and recurring-cost presentation.
- Friday email, safe sharing, and token-scoped mobile feedback.
- Progressive noise, green-space, building-scale, renovation, legal-title, and
  developer/project evidence.
- VPS deployment, monitoring, and encrypted NAS backup.

### Explicit non-goals

- Contacting sellers, agents, banks, developers, or contractors automatically.
- Making a purchase, submitting an offer, or changing profile hard rules without
  buyer approval.
- Certifying title, mortgage safety, developer solvency, completion, valuation,
  mortgage affordability, or renovation price.
- Bypassing CAPTCHA, anti-bot controls, authentication, or source restrictions.
- Crawling the whole web indiscriminately.
- A multi-user SaaS product, native mobile application, or self-hosted mail server.

## 3. Working assumptions and remaining decisions

- The first production sources are Otodom, OLX, Morizon, and Gratka, subject to a
  documented access review for each.
- Portal alerts are the discovery trigger; page fetching is an adapter-specific,
  separately approved capability.
- Primary-market deadline is provisionally 30 August 2029. It remains to decide
  whether handover, habitability after finishing, and separate-title transfer must
  all occur by that date.
- When fewer than 10 compliant properties exist, the digest sends all worthwhile
  compliant properties and up to 10 exploration results rather than padding.
- Mixed park-and-ride routing is deferred until provider behavior and buyer need
  are verified.
- Profile weights in ADR 0007 are initial hypotheses and require pilot review.

None of these except source access blocks creation of the core using fixtures and
fake providers.

## 4. Repository decision

Use **one private repository** containing application code, adapters, migrations,
tests, deployment manifests, and documentation.

Suggested structure:

```text
homez/
  pyproject.toml
  src/homefinder/
    domain/          # profile, eligibility, scores, costs, selection
    application/     # use cases and transaction boundaries
    sources/         # Gmail and approved portal adapters
    catalog/         # listings, snapshots, deduplication
    enrichment/      # routes, location, environment, renovation, risk evidence
    digest/          # rendering, sharing, delivery
    feedback/        # scoped-token forms and preference events
    operations/      # CLI jobs, quota, health, audit, retention
    web/             # minimal FastAPI feedback/health surface
  migrations/
  tests/
    unit/
    integration/
    contract/
    e2e/
    fixtures/        # sanitized alert/page/provider fixtures
  infra/
    compose.yaml
    backup/
    monitoring/
  docs/real-estate-assistant/
```

Build one application image and run different commands for the web endpoint and
scheduled jobs. Use PostgreSQL/PostGIS as the only production datastore.

Do **not** create separate repositories for adapters, browser worker, personal
configuration, or infrastructure yet. Split only if a component later has an
independent release/security owner or the generic engine is open-sourced. Secrets,
tokens, buyer data, database dumps, and generated reports remain outside Git.

## 5. Acceptance criteria

### Acquisition and catalog

- An approved alert email is processed idempotently by immutable message ID.
- Unsupported, malformed, or suspicious messages are quarantined without losing
  the original.
- Every external fetch follows an enabled source policy, rate limit, allowlist,
  timeout, and safe redirect rule.
- The same inferred property is not presented twice because it appears on several
  portals; uncertain duplicate links remain reviewable.
- Price, availability, description, and material facts retain dated snapshots.

### Matching and reporting

- Hard filters produce explicit pass/fail/unknown reasons; score cannot override
  a failed rule.
- Every score exposes its components, evidence confidence, and important missing
  data.
- The Friday report selects up to 10 compliant and 10 varied exploration listings
  after deduplication, cooldown, and material-change checks.
- Each exploration item names every failed rule and threshold distance.
- Effective all-in price includes known mandatory parking/storage, fees,
  commission, finishing/renovation high estimate and contingency as applicable.
- Reports send once per scheduled period at 10:00 Europe/Warsaw and handle delayed
  recovery without duplicates.

### Routing

- Podbrzezie 6 is evaluated at weekday 08:30 arrival and 17:30 return for all
  supported modes; the slower direction controls the 45-minute rule.
- Each displayed listing shows route mode, both times, data age/confidence, and
  minutes under or over the goal.
- Transactional quota reservation prevents application calls past the configured
  safety ceiling under concurrency.
- Quota exhaustion sends one Gmail alert, suspends calls, and never turns unknown
  commute into a pass.

### Feedback and security

- Email-link `GET` is side-effect free; deliberate `POST` is required to record
  feedback.
- Tokens are random, hashed at rest, scoped to one listing/report, expire, and are
  absent from logs/referrers/third-party content.
- Safe-sharing content contains no feedback token, private note, or buyer-profile
  detail.
- Repeated feedback may adjust soft scoring only; profile changes are auditable
  and hard-rule changes require explicit buyer approval.

### Operations

- Health status shows last successful ingestion, per-source state, route quota,
  last digest, backup state, and oldest failed/pending job.
- Jobs are retryable and idempotent; partial external failures do not corrupt
  catalog state.
- An encrypted PostgreSQL backup reaches the NAS and a documented restore test
  succeeds before live delivery is enabled.

## 6. Implementation slices

Each slice starts with a failing test and ends with a deployable, reviewable
increment.

### Slice 0 — Repository and delivery foundation

**Status:** Completed 30 August 2026. CI validates formatting, linting, strict
typing, tests, dependency vulnerabilities, architecture boundaries, migrations
against PostGIS, image construction, Compose startup, and container health using
fake credentials.

**Coding agent:** initialize Python project, formatting/lint/type/test tools,
FastAPI health endpoint, PostgreSQL/PostGIS migration framework, Docker Compose,
CI, configuration schema, secret placeholders, and architecture boundary tests.

**Buyer:** create/approve the private repository; provide target VPS/NAS platform
details without committing credentials.

**Exit:** clean checkout passes unit tests, lint, type checks, migrations, and
container health checks using fake credentials.

### Slice 1 — Walking skeleton with sanitized fixtures

**Status:** Completed 30 August 2026 with a synthetic fixture using reserved
`.invalid` domains. Real portal fixtures remain buyer input for Slice 2 contract
work.

**Coding agent:** implement email-message, listing, snapshot, source, and property-
candidate models; parse one sanitized alert fixture; normalize it; render a local
preview card; add idempotency and malformed-input tests.

**Buyer:** provide one representative alert from each chosen portal with personal
addresses/tokens removed, or authorize local sanitization.

**Exit:** a fixture travels from raw alert to normalized listing and preview in
one integration test.

### Slice 2 — Gmail ingestion and source governance

**Status:** Implemented 31 August 2026 with a fake Gmail contract and the
sanitized sample-portal alert. Live OAuth consent, real portal fixtures, and
source-by-source terms approval remain buyer prerequisites.

**Coding agent:** Gmail OAuth adapter, label/state workflow, polling CLI, encrypted
token loading, source-policy registry, quarantine/retry behavior, source health,
and contract tests for each approved alert format.

**Implemented:** `GmailApiClient` reads and modifies Gmail messages through the
Gmail REST API; `EncryptedTokenStore` stores OAuth token JSON using AES-GCM; and
`GmailPollingService` polls a configured label, ingests by immutable provider
message ID, applies processed/quarantine labels, preserves malformed raw mail,
records per-source health, and leaves transient failures available for retry.
The `poll-gmail` CLI currently registers only the synthetic sample source, whose
page-fetch policy is disabled. Additional portal parsers and policies must be
added only after their alert fixtures and access methods are approved.

**Buyer required:** create dedicated Gmail and portal accounts, enable 2FA, create
saved searches, complete one-time OAuth consent, and approve each source's access
method after terms review.

**Exit:** fixture-backed Gmail polling ingests without duplicate processing and
does not fetch pages for an unapproved source. Live-alert exit evidence is
blocked until the buyer completes OAuth consent and supplies approved alert
fixtures for the selected portals.

### Slice 3 — Catalog history and deduplication

**Status:** Implemented 31 August 2026 with SQLite-backed regression tests and
an Alembic migration. Deterministic identity links matching normalized alerts
to one candidate, while conservative fuzzy matches remain pending review.

**Implemented:** immutable listing snapshots, stable duplicate keys, duplicate
confidence/reason evidence, explicit candidate merge/split operations, and
presentation history with cooldown/material-change resurfacing decisions.
Fuzzy evidence is never auto-merged, and manual operations preserve every
listing and snapshot for auditability.

**Coding agent:** snapshots, availability transitions, price changes, deterministic
duplicate keys, fuzzy duplicate evidence, manual merge/split controls, cooldowns,
and material-change rules.

**Buyer:** review a sample of suggested duplicate groups and false positives.

**Exit:** cross-portal fixture-backed alerts create one candidate with multiple
listings; changed snapshots resurface immediately, unchanged dismissed items
respect cooldown, and fuzzy duplicate suggestions remain reviewable.

### Slice 4 — Profile, costs, eligibility, and explainable ranking

**Status:** Implemented 31 August 2026 as a pure domain increment. The active
profile is versioned and immutable; eligibility uses explicit pass/fail/unknown
states, while conservative cost estimates, weighted components, confidence, and
exploration reasons are retained in the result. No listing or buyer profile is
persisted by this slice yet.

**Implemented:** `BuyerProfile` captures the current discovery defaults, including
the 45-minute commute goal, 40 m² minimum, 48–55 m² preference band, PLN 800,000
stretch cap, and weighted preference components. `CostEstimate` exposes
acquisition, closing, and low/base/high move-in totals. `evaluate` applies hard
rules without allowing score to override failure; missing facts remain unknown
and reduce confidence. `select_slate` returns separate, capped compliant and
exploration results, with deterministic ordering and locality diversification.
Exploration excludes rentals, known serious legal risk, and non-vacant homes.

The PLN 800,000 cap and unresolved primary-market deadline remain provisional
until buyer approval. Affordability is an estimate, not mortgage advice; the
caller must provide the equity/closing amount applicable to its financing model.

**Coding agent:** encode the versioned profile from the discovery docs; implement
hard tri-state rules, effective all-in cost, initial weighted score, confidence,
10+10 diversified slate selection, and full explanation output.

**Buyer required:** approve a generated profile snapshot, resolve remaining hard
deadline questions, and review examples near every threshold.

**Exit:** golden tests map representative listings to expected eligibility,
component scores, and exploration reasons.

### Slice 5 — Geocoding and multimodal routes

**Coding agent:** Google Routes adapter, batching/cache, destination/routing-goal
versions, morning/return evaluation, quota ledger/reservation, circuit breaker,
alerts, fake-provider tests, and stale-route behavior.

**Buyer required:** create the dedicated Google Cloud project, enable billing,
restrict credentials, set Google-side quotas/alerts, and approve the current free
allowance plus application safety ceiling.

**Exit:** concurrency tests prove the local ceiling cannot be exceeded; sandbox
requests validate Krakow transit/drive/walk/bicycle output before production use.

### Slice 6 — Digest, safe sharing, and feedback loop

**Coding agent:** responsive HTML/plain-text templates, Friday scheduling,
idempotent delivery, 10+10 sections, share-safe `mailto`/copy content, token model,
mobile form, feedback events, CSRF protection, rate limits, and audit history.

**Buyer:** provide recipient address and approve email/mobile previews on real
devices; decide domain and authorize DNS/TLS configuration.

**Exit:** end-to-end test sends to a test inbox, records feedback only on POST,
prevents token reuse, and confirms share content contains no private token.

**First usable release:** enable weekly delivery after Slices 0-6 and a buyer-
approved shadow report. Do not wait for every enrichment below.

### Slice 7 — Environmental and building enrichment

**Coding agent:** address confidence, road/noise indicators, green-space distances,
floor/elevator consistency, entrance-level dwelling estimates, evidence freshness,
and manual correction UI. Start with official/open datasets and conservative
heuristics; keep unknown distinct from negative.

**Buyer:** validate results against known Krakow examples and add viewing feedback
for actual noise, entrance size, and surroundings.

**Exit:** enrichment cannot turn missing evidence into a hard rejection and every
derived fact identifies its source/date.

### Slice 8 — Renovation and comparable workflow

**Coding agent:** itemized scope, low/base/high estimates, contingency, comparable
selection/explanation, conservative eligibility rule, habitability checklist,
and quote/document attachments metadata.

**Buyer required:** choose desired finish standard; provide inspection results and
contractor quotations for serious candidates; approve comparable sets.

**Exit:** only high estimate plus contingency controls normal eligibility, and
weak comparables produce low confidence/manual review.

### Slice 9 — Primary-market risk dossier

**Coding agent:** developer/project/entity/evidence model, KRS/permit/DFG/prospectus
checklists, dated risk dimensions, document ingestion where permitted, manual task
queue, and digest rendering.

**Buyer/professional required:** obtain CAPTCHA-protected or non-public documents,
prospectus and contract; commission legal/financial review before commitment.

**Exit:** the system distinguishes SPV/parent/contractor, never claims guaranteed
completion, and blocks normal ranking when critical evidence is insufficient.

### Slice 10 — Production hardening and pilot

**Coding agent:** restricted containers, database isolation, retention jobs,
structured redacted logs, monitoring, encrypted NAS backup, restore automation,
dependency/security scanning, runbooks, and failure drills.

**Buyer required:** authorize VPS/NAS deployment and firewall/DNS changes, securely
enter secrets, receive a test failure notification, witness backup restoration,
and approve live Friday delivery.

**Exit:** four-week pilot completes with weekly review of misses, false positives,
duplicates, source failures, quota usage, and proposed soft-weight adjustments.

## 7. Attention and ownership matrix

| Work | Coding agent | Buyer | External professional |
| --- | --- | --- | --- |
| Code, migrations, tests, containers, CI, docs | Own | Review outcomes | — |
| Gmail/portal/Google account creation and terms acceptance | Prepare instructions/integration | Own | — |
| Source access approval | Research and document | Decide/accept | Legal advice if uncertain |
| Secrets and production credentials | Provide secure input mechanism | Enter/rotate | — |
| Preference/profile encoding | Implement and explain | Approve | — |
| Soft-weight tuning | Analyze feedback/propose | Approve | — |
| Hard-rule changes | Never automatic | Own | — |
| VPS, DNS, firewall, NAS deployment | Automate after authorization | Authorize/access | — |
| Listing extraction and risk flags | Automate with confidence | Review serious candidates | — |
| Mortgage capacity and loan choice | Scenario tooling only | Decide | Broker/bank/adviser |
| Title, mortgage, developer contract | Checklist/evidence organization | Commission/review | Lawyer/notary/bank |
| Renovation cost | Preliminary range/workflow | Define standard/get access | Inspector/contractors |
| Seller/agent contact, viewings, offers | Never autonomous | Own | Agent/lawyer as chosen |

The coding agent can complete nearly all repository work and prepare deployments,
but it must stop for account consent, source authorization, production secret
entry, infrastructure-changing approval, profile/business decisions, and any
legal/financial commitment.

## 8. Test-first plan

The first failing test is:

> Given one sanitized supported alert, ingesting the same Gmail message twice
> creates one source message, one listing snapshot, and one preview card.

### Unit tests

- Profile validation and versioning.
- Every hard rule at pass/fail/unknown boundaries.
- Cost and annuity calculations, currency/area normalization, and renovation rule.
- Score components, missing-data confidence, cooldown, material change, and
  diversified slate selection.
- Duplicate fingerprints and merge/split invariants.
- Route goal aggregation and quota reservation under concurrency.
- Token expiry/scope/use and safe-share redaction.

### Integration and contract tests

- PostgreSQL migrations and transactional job state.
- Gmail/Routes adapters against fakes plus minimal approved sandbox calls.
- Sanitized portal email/page fixtures with change-detection alerts when parsers
  break.
- HTML/plain-text email snapshots and mobile viewport checks.
- Scheduler retry, delayed send, daylight-saving, and exactly-once behavior.
- Backup creation and restore into a clean database.

### End-to-end/manual acceptance

- Portal alert -> Gmail -> catalog -> enrichment -> shadow 10+10 report -> mobile
  feedback -> changed next report.
- Quota-exhaustion drill and email alert.
- Expired/forwarded/scanner-opened token behavior.
- Source outage, malformed email, duplicate listing, hidden address, and empty
  compliant pool.
- Buyer reviews at least two shadow reports before live weekly sending.

## 9. Security review gate

Before live deployment, verify:

- OAuth/API keys are least-privilege, restricted, encrypted, rotated, and absent
  from Git, images, logs, reports, and backups unless backups are encrypted.
- All external URLs are allowlisted/validated against SSRF, redirect, and local-
  network access; fetched content is size/time/type limited.
- Email/HTML/source content is treated as untrusted and escaped/sanitized against
  XSS, template injection, unsafe files, and prompt injection if AI extraction is
  later introduced.
- Feedback authorization, POST-only mutation, CSRF, rate limiting, token hashing,
  log redaction, and referrer policy are tested.
- SQL uses bound parameters/ORM; job claims and quota reservations are safe under
  concurrency.
- Containers/database are not public, dependencies/images are scanned, and
  restore/revocation/runbooks are exercised.

## 10. Verification and commit plan

For every slice:

1. Add the named failing test.
2. Run the narrowest test and confirm the expected failure.
3. Implement the smallest passing vertical behavior.
4. Run unit, integration, lint, format, type, migration, and security checks
   appropriate to the change.
5. Review diff/status for secrets, fixtures, generated files, and unrelated edits.
6. Commit one coherent slice using messages such as:
   - `chore(platform): establish tested service foundation`
   - `feat(ingestion): import Gmail listing alerts idempotently`
   - `feat(ranking): select explainable compliant and exploration slates`
   - `feat(routing): enforce quota-safe multimodal commute checks`
   - `feat(feedback): add scoped mobile digest feedback`

Do not combine all milestones into one large commit. Tag the first live shadow
release, record database migration/rollback notes, and keep deployment approval
separate from code completion.

## 11. Recommended next action

Slice 3 is ready for buyer review of pending duplicate groups and false
positives. The next coding increment is Slice 4 (profile, costs, eligibility,
and explainable ranking); live source credentials and production deployment
remain outside the current delivery boundary.
