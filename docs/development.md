# Development

## Setup and checks

Use Python 3.10 or newer in an isolated environment:

```bash
python -m venv .venv
.venv/bin/python -m pip install --constraint requirements.lock -e '.[dev]'
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest
.venv/bin/pip-audit --requirement requirements.lock --no-deps --disable-pip
.venv/bin/yamllint -d relaxed .github/workflows .github/dependabot.yml
```

## Walking-skeleton preview

The committed sample is synthetic and uses reserved `.invalid` domains. It
contains no personal data or working token.

All committed `.eml` files must use reserved `.invalid` or `example.com`,
`example.net`, or `example.org` addresses and URLs. Raw exports from a mailbox
must never be added to Git, even temporarily: transport headers, tracking URLs,
unsubscribe links, and message identifiers can identify the recipient after the
visible address is replaced. Check fixtures locally with:

```bash
.venv/bin/python -m homefinder.fixture_safety \
  data/email_examples tests/fixtures
```

CI runs the same check. New parser fixtures must be manually authored as minimal
synthetic contract examples; do not generate them from captured pages. Follow the
[alert fixture cleanup runbook](security/alert-fixture-cleanup.md) if unsafe mail
has entered Git history.

```bash
.venv/bin/homefinder preview \
  tests/fixtures/sample_portal/valid_alert.eml \
  --output preview.html
```

Opening `preview.html` shows the normalized listing card. Running the command
again against the default local database is idempotent.

Catalog history and deduplication are covered by
`tests/unit/test_slice3_catalog.py`. Exact duplicate identity is automatic only
for matching normalized alert facts; fuzzy matches are retained as pending
evidence for buyer review. Candidate merge, split, and resurfacing decisions
are repository operations and do not discard listing snapshots.

## Profile and ranking

Slice 4 uses `homefinder.domain.profile`, `homefinder.domain.costs`,
`homefinder.domain.matching`, and `homefinder.domain.ranking`. Transaction type
(`purchase`/`rental`) is independent from market type
(`primary`/`secondary`), so a primary-market purchase may pass after its dossier
passes. Missing hard-rule facts remain `unknown`; only all-pass results are
compliant. `CostEstimate.effective_all_in_high_minor` includes purchase price,
mandatory extras, closing costs, high works, and contingency. Acquisition cash
is calculated separately by subtracting the explicitly financed purchase value.
Every failed or unknown rule exposes its actual value, threshold, and distance.

Both compliant and exploration selection use locality diversification,
presentation cooldown, and material-change resurfacing. Buyer profiles are
stored as immutable versions by `SqlAlchemyBuyerProfileRepository`; a draft is
not active until a human explicitly records approval. No migration seeds an
approved profile, preserving issue #27 as the live-ranking activation gate.
Golden cases are in `tests/unit/test_slice4_matching.py` and
`tests/unit/test_buyer_profile_repository.py`.

Dependabot groups Python dependency upgrades into one pull request so the
declaration and `requirements.lock` move together. Before merging dependency
updates, refresh the branch onto `main` and require both CI jobs to pass; this
also exercises the current Alembic metadata against PostGIS and the Compose
image rather than validating an obsolete base revision.

## Gmail polling

Gmail polling uses the single least-privilege `gmail.modify` scope. Complete the
one-time consent flow with offline access, then store the resulting token envelope
with `EncryptedTokenStore`. The encrypted token, its base64 AES key, and the
reviewed source policy are mounted as private (`0600`) regular files. Secret
values are never accepted as command-line arguments:

```bash
HOMEFINDER_ENVIRONMENT=production \
HOMEFINDER_DATABASE_URL='postgresql+psycopg://...' \
HOMEFINDER_GMAIL_TOKEN_FILE=/run/secrets/homefinder_gmail_token \
HOMEFINDER_GMAIL_TOKEN_KEY_FILE=/run/secrets/homefinder_gmail_token_key \
HOMEFINDER_GMAIL_SOURCE_POLICY_FILE=/run/secrets/homefinder_source_policy \
.venv/bin/homefinder poll-gmail --source otodom
```

The policy file contains a `sources` object keyed by portal, with `enabled`,
reviewed `allowed_senders` addresses, direct-listing `allowed_hosts`, and an
optional `max_message_bytes`. The command resolves/creates mailbox-scoped alert,
processed, quarantine, and retry labels and persists Gmail's actual label IDs.
Expired access tokens refresh automatically without logging credentials. Only
messages on the resolved alert label and matching the sender/size/source contract
are parsed or modified. Page fetching remains disabled.

The human tasks #29–#32 remain mandatory: grant only `gmail.modify`, place the
encrypted token and separate key through the secret mechanism, and perform one
reviewed sandbox poll confirming unrelated mail is untouched. Revoke the OAuth
grant and recreate the encrypted token if either credential file may have been
exposed.

Sanitized parser contracts for Otodom, Morizon, Gratka, and OLX are documented in
[`portal-contracts.md`](real-estate-assistant/portal-contracts.md). Sender
allowlists—not a private source header—select the source. These contracts do not
enable page fetching.

## Migrations

For a local SQLite smoke test:

```bash
DATABASE_URL=sqlite:///migration-check.sqlite3 .venv/bin/alembic upgrade head
```

Production uses PostgreSQL/PostGIS through `infra/compose.yaml`.

## Persistent workflow

The alert-to-report path is coordinated by durable, idempotent jobs. Run
`homefinder reconcile-workflow` after polling to recover any catalog commit that
occurred before its successor job was queued, then run workers with a stable
operational identifier:

```bash
homefinder enqueue-poll \
  --source otodom --scheduled-at 2026-09-04T07:00:00+00:00
homefinder workflow-worker --worker-id worker-1 --max-jobs 100
homefinder workflow-status
homefinder enqueue-report \
  --period 2026-W36 \
  --cutoff-at 2026-09-04T08:00:00+00:00 \
  --routing-goal-version 1
```

Jobs use fenced leases, bounded deterministic backoff, attempt history,
dead-letter/manual-review states, and idempotency keys. Normalized facts, match
explanations, buyer-profile/routing versions, and prepared report bodies are
immutable persisted artifacts. Missing hard evidence remains `unknown` and is
excluded from the compliant section. Draft preparation does not record a
presentation; delivery acknowledgement in the later delivery slice owns that
side effect.

## Production hardening

Slice 10 provides `homefinder.operations` for redacted JSON logs, component/job
health snapshots, and AES-GCM encrypted PostgreSQL dump/restore plus retention
commands. These are tested deployment contracts; they do not enter credentials,
copy files to a NAS, or authorize production changes. Follow
[`docs/operations.md`](operations.md) for the restore drill and deployment gates.

## Runtime configuration

Configuration is validated at application startup:

- `HOMEFINDER_ENVIRONMENT`: `development`, `test`, or `production`;
- `HOMEFINDER_DATABASE_URL`: SQLAlchemy database URL, treated as a secret;
- `HOMEFINDER_LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`.

Production mode rejects non-PostgreSQL database URLs. Compose constructs the
database URL from `POSTGRES_PASSWORD`; the real `.env` must remain outside Git.
Tests use the schema directly and CI validates migrations against real PostGIS.

## Weekly digest and feedback

Slice 6 provides renderer and delivery/feedback contracts in `homefinder.digest`.
`render_digest` returns HTML and plain text with separate compliant and
exploration sections; `render_share_text` is safe to copy or use as `mailto`
content and never includes feedback tokens. Tokens are random at issue time,
stored as SHA-256 hashes, scoped to one report/listing, expiring, and single-use.
Feedback mutation is POST-only and checks CSRF before recording an auditable
event. The mobile GET form never receives the capability token at the server:
private links carry it in the URL fragment, remove the fragment from browser
history, and submit it only with the deliberate POST. Responses disable caching
and referrers; the CSRF cookie is Secure, HttpOnly, SameSite=Strict, and narrowly
path-scoped. Configure `HOMEFINDER_FEEDBACK_RATE_SALT_FILE` with a private random
value used only to pseudonymize rate-limit actors. Configure an approved HTTPS
origin in `HOMEFINDER_FEEDBACK_BASE_URL` and a separate, stable 32-byte-or-longer
secret in `HOMEFINDER_FEEDBACK_TOKEN_KEY_FILE`. The delivery worker derives the
same private capability after a retry while storing only its hash; it adds links
only to HTML and preserves the token-free plain-text share representation. The
persistent outbox claims
prepared drafts with a fenced token, retries
only pre-acknowledgement failures, and reuses a stable provider idempotency key
after stale-claim recovery. `schedule-delivery` calculates the most recent due
daily 17:00 `Europe/Warsaw` period, including DST and delayed recovery;
`delivery-worker` sends through Mailtrap's HTTPS API and requires a successful
response containing exactly one provider message ID. Configure only secret-file
paths for the recipient and provider token. Do not put feedback URLs in shared
content. The adapter supports the Mailtrap sandbox and transactional production
endpoint shapes, records the stable delivery key as a Mailtrap custom variable,
and rejects other endpoint hosts or paths.

```bash
homefinder schedule-delivery
homefinder delivery-worker --max-deliveries 10
```

The authenticated buyer UI exposes active criteria at `/feedback/settings`.
Submitting the form creates and approves a new immutable buyer-profile version;
the next scheduler reconciliation enqueues fresh match evaluations for that
version. The page uses the same Basic-auth secret as `/feedback/offers`, requires
a same-site CSRF cookie, and keeps transaction-safety rules read-only.

Homez also sends the stable key in `Idempotency-Key`, but Mailtrap does not
currently document that the header deduplicates requests. Ambiguous timeout
retries can therefore duplicate a message. The recipient remains blocked on
human task #33 and live delivery remains blocked on #69 plus explicit acceptance
or mitigation of this residual risk. Test-inbox HTML/plain rendering evidence
must be recorded before activation.

## Environmental and building enrichment

Slice 7 provides `homefinder.enrichment.environment` as a provider-independent
boundary for open-data adapters. `EnvironmentalEnricher` records evidence source,
observation date, and confidence for address, road/noise, green-space, floor,
elevator, and building-scale facts. Missing values remain unknown; moderate noise
is not converted into a false quiet result, and environmental facts cannot create
a hard matching rejection. Evidence older than the configured freshness window
must be treated as stale by callers.

The minimal manual correction contract is `POST /corrections/{property_id}` with
`field`, `value`, `corrected_by`, and `reason`. Corrections are append-only and
auditable. The endpoint requires `Authorization: Bearer ...`, with the expected
value read from `HOMEFINDER_ADMIN_BEARER_TOKEN_FILE`; it fails closed if the
secret is unavailable. The current correction store is suitable for development
only; production persistence must use migration `20260831_07` before exposure.

## Renovation and comparable workflow

Slice 8 provides `homefinder.enrichment.renovation`. Build an estimate from
`RenovationItem` packages and pass it to `RenovationWorkflow.assess` with dated
`Comparable` evidence. The workflow selects comparables at or above its
similarity threshold (0.70 by default), calculates the adjusted advantage using
the high estimate plus contingency, and returns `PASS`, `FAIL`, or `UNKNOWN`.
`UNKNOWN` is expected for weak evidence or an incomplete
`HabitabilityChecklist`; callers must route it to manual review. Use
`AttachmentMetadata` for quote/inspection metadata only. Persistence is
prepared by migration `20260831_08`; uploaded files need protected external
storage and authorization before production use.

## Primary-market risk dossier

Slice 9 provides `homefinder.enrichment.primary_market`. Build a
`PrimaryMarketDossier` with separate `ProjectEntity` records for the contracting
SPV, parent group, contractor, and project. Attach only permitted `Evidence`
references and dated `RiskAssessment` facts; the model stores metadata and
references rather than silently downloading protected documents.
`normal_eligibility` is `UNKNOWN` when critical checks are missing and `FAIL`
for serious legal risk or other higher-concern dimensions. Use `ManualTaskQueue`
for CAPTCHA-protected or otherwise non-public verification.

For primary listings, pass the dossier's `normal_eligibility` into
`PropertyFacts.primary_market_eligibility`; matching then prevents an incomplete
dossier from entering the compliant slate. A `DigestItem` may carry the dossier
to show concern and missing critical checks. Migration `20260831_09` is the
persistence boundary; production needs authorized register access, protected
document storage, authentication for task review, and independent legal/
financial review.

## ADR 0017 central scrape coordinator (Slice 1)

The concurrent path remains disabled by default. Slice 1 adds an internal queue
and worker API; it does not switch workflow normalization, activate a parser, or
release the production backlog. Keep the flag off until the subsequent worker,
source-budget, artifact, and release gates are satisfied.

The coordinator owns PostgreSQL sessions. Workers receive only their individual
bearer credential and the private coordinator address. The credential registry
is a private secret file selected by
`HOMEFINDER_COORDINATOR_CREDENTIALS_FILE`: a JSON array of entries with an
`identity` object (`worker_id`, `source`, `deployment`) and a
`token_sha256` digest. Each credential binds exactly one NAS or VPS worker to
one portal; duplicate identities/digests, unsafe file permissions, and invalid
registries fail closed. Tokens belong in worker secret files, never task payloads
or database rows. Production credential creation and provisioning remain
deployment gates.

With `HOMEFINDER_CONCURRENT_SCRAPING_ENABLED=true`, a credential-file path, and
`HOMEFINDER_SOURCE_BUDGET_POLICY_FILE`, the web app mounts these private routes
under `/internal/scrape/v1`. The policy file is bounded, rejects unknown keys,
and must contain exactly Gratka, Morizon, Otodom, and OLX:

```json
{
  "billing_cycle_anchor_day": 15,
  "portals": {
    "gratka": {"minimum_interval_seconds": 10, "daily_attempt_limit": 1100, "daily_success_limit": 1000, "policy_version": "reviewed-2026-09"},
    "morizon": {"minimum_interval_seconds": 10, "daily_attempt_limit": 1100, "daily_success_limit": 1000, "policy_version": "reviewed-2026-09"},
    "otodom": {"minimum_interval_seconds": 10, "daily_attempt_limit": 1100, "daily_success_limit": 1000, "policy_version": "reviewed-2026-09"},
    "olx": {"minimum_interval_seconds": 10, "daily_attempt_limit": 1100, "daily_success_limit": 1000, "policy_version": "reviewed-2026-09"}
  }
}
```

The values above demonstrate the reference capacity profile; they are not
production approval. Record the portal-policy review and Webshare billing anchor
before supplying the production file. The web process reads it directly; workers
never receive this file or database credentials.

The private routes are:

- `POST /workers/heartbeat`: bounded executable release hashes and health.
- `POST /claim`: optional production task classes; source and deployment come
  from authentication.
- `POST /heartbeat`, `/succeed`, `/fail`, `/defer`: current lease and bounded
  typed metadata only.
- `GET /status`: source-scoped safe metadata, with `limit` and opaque `before`
  cursor pagination.

No route enqueues work, releases recovery, activates a parser, or accepts raw
response content. The public Caddy route remains restricted to `/feedback/*`.
Tailnet access to the coordinator is not provisioned by this slice.

The queue pins work to the current portal release/epoch, requires a healthy
advertising worker, prioritizes live tasks over recovery, and uses PostgreSQL
`FOR UPDATE SKIP LOCKED` for claims. A source pointer is share-locked through
claim/acknowledgement transactions, so future activation updates serialize with
them. Expired, foreign, already completed, and withdrawn-epoch leases are rejected.
The server checks lease time again after acquiring locks. Network recovery is
created in `held` state with no release endpoint in this slice.

Validated defaults (all with the `HOMEFINDER_` prefix):

| Setting | Default |
| --- | ---: |
| `SCRAPE_LEASE_SECONDS` | 60 |
| `SCRAPE_HEARTBEAT_SECONDS` | 20 |
| `SCRAPE_MAX_LEASE_SECONDS` | 600 |
| `SCRAPE_WORKER_HEALTH_SECONDS` | 90 |
| `SCRAPE_MAX_ATTEMPTS` | 8 |
| `SCRAPE_METADATA_RETENTION_DAYS` | 30 |

Heartbeats must precede expiry; renewal cannot exceed the maximum lease lifetime.
Workers must also refresh their capability heartbeat. Transport failures retry
with bounded backoff; parser failures are terminal, and expired attempts are
audited before replacement. The metadata-retention setting is reserved for the
later purge implementation; it does not change capture/result retention.

Migration `20260909_23` expands the dark queue and creates worker, attempt, and
empty activation-pointer tables. It seeds no active release. The pointer is
introduced before Slice 7 solely to fence queue transactions; audited activation
and rollback tooling still belong to Slice 7. Legacy workflow/report tables and
the fallback path are unchanged.

Run focused checks without contacting portals:

```bash
.venv/bin/pytest tests/unit/test_scrape_queue.py \
  tests/unit/test_scrape_coordinator_api.py tests/architecture
# TEST_POSTGRES_URL must identify a disposable local/CI PostGIS database.
.venv/bin/pytest tests/integration/test_scrape_queue_postgres.py
```

PostgreSQL-marked tests truncate application tables through the existing test
fixture. Never point them at a production or otherwise valuable database.
