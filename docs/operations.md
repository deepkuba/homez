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
