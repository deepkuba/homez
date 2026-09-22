# Operations runbook

Slice 10 adds operational contracts without enabling live infrastructure. Keep
the backup key outside the VPS and NAS backup directory.

## Health and logs

`GET /health` returns overall status, each registered component’s state/check
time, and the oldest pending job. Failed or unknown components produce
`degraded`. Application logs are newline-delimited JSON. Fields indicating a
password, token, secret, authorization value, API key, or database URL are
redacted; raw email, OAuth responses, listing pages, and backup keys must not be
logged.

The authenticated scraper-error dashboard is available at
`/feedback/scraper-errors`. It opens with the 50 newest persisted `poll` and
`normalize` attempt failures, loads older history through a stable cursor, and
uses a same-origin server-sent event stream for newly persisted errors. The
dashboard deliberately exposes only bounded error codes/details and resolved
portal names; job payloads, listing URLs, credentials, and raw container logs
remain private.

## Encrypted PostgreSQL backups

Generate a 32-byte key once and store it in a secret manager separate from the
database and NAS:

```bash
openssl rand -base64 32
HOMEFINDER_DATABASE_URL_FILE=/run/secrets/database_url \
HOMEFINDER_BACKUP_KEY_FILE=/run/secrets/backup_key \
homefinder backup /var/backups/homefinder/$(date -u +%Y%m%dT%H%M%SZ).dump.enc
```

This invokes `pg_dump` without a shell, encrypts the custom-format dump with
AES-GCM, writes mode `0600`, and atomically replaces the destination. Copy the
encrypted file to the restricted NAS account only after infrastructure approval.

Restore into a clean or explicitly disposable database:

```bash
HOMEFINDER_DATABASE_URL_FILE=/run/secrets/test_restore_database_url \
HOMEFINDER_BACKUP_KEY_FILE=/run/secrets/backup_key \
homefinder restore /var/backups/homefinder/backup.dump.enc
```

Retention removes only `*.dump.enc` files older than the selected period:

```bash
homefinder prune-backups /var/backups/homefinder --keep-days 14
```

The restore drill is complete only when a clean database accepts the dump and a
representative application read succeeds. Record the date, image commit,
migration revision, backup filename, and result outside Git.

## Deployment gates

The buyer must authorize VPS/NAS paths and firewall/DNS changes, enter secrets
securely, receive a test failure notification, and review a successful restore.
Keep app and database services on the private Compose network, deploy immutable
image tags, and run migrations before starting the new app.

## Production parser retention

Run one bounded retention batch daily after migrations are current:

```bash
homefinder run-parser-retention --batch-size 500
```

The command deletes expired parser details in capture-time order, records any
remaining expired backlog as unhealthy, measures the full database, and sends
content-free operational alerts through the configured mail transport. Schedule
only one instance. A failed run alerts immediately and then at most daily; the
next successful run sends one recovery message. Review the aggregate retention
state and oldest remaining capture before increasing the batch size.

Each encrypted database backup now has a sibling `*.manifest.json` file. Keep it
with the encrypted dump. Restore prints its backup date, oldest included parser
capture, and the current live two-year cutoff before continuing. The warning
does not delete backup data or block restore; backup pruning remains a manual
operator decision.

## Parser bootstrap discovery canary

Use the discovery canary only when a portal has no active parser and therefore
cannot yet produce the raw evidence required by the activation gate. The
command selects at most 25 newest non-inactive listings, registers the packaged
parser only as a draft, and enqueues artifact-only work. Discovery workers use
the normal central pacing, cooldown, proxy accounting, and bounded HTTP path.
They store exact encrypted raw bytes on NAS and safe capture metadata in the
database; they do not run the parser or write production facts.

Preview first. `--git-commit` and `--image-digest` must identify the exact
deployed immutable worker image:

```bash
homefinder release-discovery-canary \
  --source gratka \
  --git-commit REVIEWED_COMMIT \
  --image-digest sha256:REVIEWED_DIGEST
```

After reviewing the selected count, explicitly release the canary with a named
operator:

```bash
homefinder release-discovery-canary \
  --source gratka \
  --git-commit REVIEWED_COMMIT \
  --image-digest sha256:REVIEWED_DIGEST \
  --execute --actor OPERATOR_ID
```

The limit cannot exceed 25 and execution writes an audit row. A discovery task
requires an advertised draft release but no active parser pointer. Its
completion requires an artifact identifier and cannot create a
`production_parser_results` row. Review the captured objects only through the
scoped maintenance path, freeze the benchmark manifest, and complete benchmark
review before any separate manual activation.

## NAS artifact retention and backup boundary

The optional artifact service stores encrypted diagnostic objects and their
wrapped per-object keys only under `HOMEZ_ARTIFACT_DATA_DIR` on the NAS. The
wrapping key and scoped API identities arrive through read-only secret files
from `HOMEZ_ARTIFACT_SECRETS_DIR`; read audit records use the separate
`HOMEZ_ARTIFACT_AUDIT_DIR` mount. The PostgreSQL backup service mounts none of
these paths or secrets. PostgreSQL may retain safe artifact references and
tombstones, never raw HTML, ciphertext, or artifact decryption keys.

Exclude the data and secret directories from all file backups, NAS snapshots,
replication, and backup-account permissions. Do not nest them beneath database
backup staging, scraper state, or any recursively backed-up parent. Check the
resolved host paths and actual backup jobs before enabling collection. The
architecture test verifies the Compose boundary; it cannot verify NAS backup
configuration. Losing the unbacked-up KEK makes retained objects unreadable.

Artifacts expire after 30 days. The service runs retention at startup and then
every 3600 seconds (hourly), deleting expired ciphertext and wrapped object
keys while retaining safe tombstones. Reads must reject expired objects even
between sweeps. Verify expiration and deletion with synthetic content before
production; monitor disk usage, sweep failures, and audit writability without
logging content or credentials. A failed read audit must prevent plaintext
from being returned. Do not loosen retention to work around a failed sweep.

To roll back, disable collection at callers and stop the `artifacts` service in
the standalone NAS project. Stopping the process also stops automated sweeps;
complete required expiry cleanup before leaving it stopped with retained data.
Do not copy retained artifacts or KEKs into a database restore or rollback
bundle. Production NAS paths, KEK provisioning, identity scopes/expiry, and
private network grants remain deployment gates until supplied and reviewed.
