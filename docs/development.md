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

CI runs the same check. New parser fixtures should be regenerated as minimal
contract examples rather than redacting a full delivered message. Follow the
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

The policy file contains a `sources` object keyed by portal, with `enabled`, one
reviewed `allowed_senders` address, one `allowed_hosts` listing host, and an
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

Sanitized parser contracts for Otodom, Morizon, and Gratka are documented in
[`portal-contracts.md`](real-estate-assistant/portal-contracts.md). OLX remains
explicitly blocked on human fixture issues #17 and #18. These contracts do not
enable a production source or page fetching.

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
event. Production delivery must provide persistent implementations backed by
migration `20260831_06`, a real mail sender, and a scheduler for Friday 10:00
`Europe/Warsaw`. Do not put feedback URLs in shared content.

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
auditable. The endpoint and in-memory store are suitable for development only;
production persistence must use migration `20260831_07` and add authentication/
authorization before exposure.

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
