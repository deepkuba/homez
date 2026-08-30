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

## Migrations

For a local SQLite smoke test:

```bash
DATABASE_URL=sqlite:///migration-check.sqlite3 .venv/bin/alembic upgrade head
```

Production uses PostgreSQL/PostGIS through `infra/compose.yaml`.
