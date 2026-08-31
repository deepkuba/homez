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

## Encrypted PostgreSQL backups

Generate a 32-byte key once and store it in a secret manager separate from the
database and NAS:

```bash
openssl rand -base64 32
homefinder backup /var/backups/homefinder/$(date -u +%Y%m%dT%H%M%SZ).dump.enc \
  --database-url "$HOMEFINDER_DATABASE_URL" \
  --encryption-key "$HOMEFINDER_BACKUP_KEY"
```

This invokes `pg_dump` without a shell, encrypts the custom-format dump with
AES-GCM, writes mode `0600`, and atomically replaces the destination. Copy the
encrypted file to the restricted NAS account only after infrastructure approval.

Restore into a clean or explicitly disposable database:

```bash
homefinder restore /var/backups/homefinder/backup.dump.enc \
  --database-url "$TEST_RESTORE_DATABASE_URL" \
  --encryption-key "$HOMEFINDER_BACKUP_KEY"
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
