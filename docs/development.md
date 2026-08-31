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

Slice 4 is implemented as a pure domain layer in `homefinder.domain.profile`,
`homefinder.domain.costs`, `homefinder.domain.matching`, and
`homefinder.domain.ranking`. `PropertyFacts` accepts normalized or enriched facts
without coupling matching to a provider. Missing hard-rule facts remain
`unknown`; only all-pass results are compliant. `CostEstimate` keeps low/base/high
renovation outcomes explicit, and `select_slate` never mixes exploration into
compliant results. Golden cases are in `tests/unit/test_slice4_matching.py`.

Dependabot groups Python dependency upgrades into one pull request so the
declaration and `requirements.lock` move together. Before merging dependency
updates, refresh the branch onto `main` and require both CI jobs to pass; this
also exercises the current Alembic metadata against PostGIS and the Compose
image rather than validating an obsolete base revision.

## Gmail polling

Slice 2 provides a governed polling command. It expects an OAuth token envelope
encrypted by `EncryptedTokenStore`; the encryption key is supplied separately as
base64 and must never be committed:

```bash
.venv/bin/homefinder poll-gmail \
  --token-file /run/secrets/homefinder-gmail-token.json \
  --encryption-key "$HOMEFINDER_GMAIL_TOKEN_KEY"
```

The current CLI registers only the sanitized `sample_portal` policy. It polls
the selected Gmail label, marks successfully handled messages with
`HOMEZ_PROCESSED`, quarantines malformed messages with `HOMEZ_QUARANTINE`, and
leaves transient failures labeled for retry. Page fetching is disabled by the
default source policy. Real portal policies must not be enabled until their
access method and terms have been reviewed and approved.

## Migrations

For a local SQLite smoke test:

```bash
DATABASE_URL=sqlite:///migration-check.sqlite3 .venv/bin/alembic upgrade head
```

Production uses PostgreSQL/PostGIS through `infra/compose.yaml`.

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
