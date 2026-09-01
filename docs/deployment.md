# Deployment

The production topology uses one immutable Homefinder image for migration, web,
workflow-worker, scheduler, and delivery-worker services. PostGIS is reachable
only on the internal backend network. Caddy is the only service with published
ports and forwards only `/feedback/*`; health and administrative routes remain
private.

Infrastructure changes remain gated by human issue #56, and production secret
entry remains gated by #58. The commands below are a reviewed procedure, not
authorization to alter the VPS, firewall, DNS, or live credentials.

## Host preparation

Create root-owned configuration and secret directories outside the checkout:

```bash
sudo install -d -m 0700 /etc/homez/secrets /etc/homez/config
sudo install -d -o 10001 -g 10001 -m 0700 /var/lib/homez
```

Create these root-owned `0600` secret files under `/etc/homez/secrets`:

- `database-url`: full SQLAlchemy PostgreSQL URL;
- `postgres-password`: the matching database password;
- `gmail-token-key`: base64 AES key for the encrypted OAuth token;
- `report-recipient`: approved recipient address;
- `mail-api-token`: approved mail-provider credential;
- `feedback-token-key`: high-entropy feedback capability signing key;
- `feedback-rate-salt`: high-entropy actor-hash salt;
- `admin-bearer-token`: high-entropy correction API credential.
- `backup-key`: a base64 32-byte backup key stored separately from backup data.

Docker mounts these files at `/run/secrets`. Their contents do not appear in
Compose interpolation, container commands, or image layers. The encrypted Gmail
token is mutable state rather than a Docker secret because refreshes atomically
replace it:

```bash
sudo install -o 10001 -g 10001 -m 0600 \
  /secure/input/gmail-token.json /var/lib/homez/gmail-token.json
sudo install -m 0600 \
  /secure/input/source-policy.json /etc/homez/config/source-policy.json
```

Copy `.env.example` to an untracked `.env`, select an immutable
`sha-<commit>` image tag, and enter only the non-secret coordinates. Validate
before pulling or starting anything:

```bash
docker compose --env-file .env -f infra/compose.yaml config --quiet
docker compose --env-file .env -f infra/compose.yaml config | \
  grep -E 'password|token|secret' || true
```

The second command may show secret *file names*, but must never show secret file
contents or a database URL.

## Immutable rollout

Record the current image digest and Alembic revision before rollout. Then:

```bash
docker compose --env-file .env -f infra/compose.yaml pull
docker compose --env-file .env -f infra/compose.yaml run --rm migrate
docker compose --env-file .env -f infra/compose.yaml up --detach --wait
docker compose --env-file .env -f infra/compose.yaml ps
```

The long-running processes handle `SIGTERM`, use interruptible waits, and write
health heartbeats. Services start only after PostGIS is healthy and migration
has completed successfully. Resource, PID, capability, filesystem, and restart
limits are declared in Compose.

Verify that HTTPS serves a feedback form while private routes return `404` at
the ingress. Do not place a real capability in shell history:

```bash
curl --fail --silent --show-error \
  "https://${HOMEZ_DOMAIN}/feedback/example-report/example-listing"
test "$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "https://${HOMEZ_DOMAIN}/health")" = 404
```

## Rollback

Rollback uses the previously recorded immutable image digest, never a mutable
tag. First stop workers and the scheduler to prevent mixed-version writes. An
application rollback is allowed only when every migration between the two
images is documented as backward-compatible. Otherwise restore into a clean
database using the reviewed recovery procedure instead of downgrading the live
schema.

```bash
docker compose --env-file .env -f infra/compose.yaml stop \
  workflow-worker scheduler delivery-worker
# Set HOMEZ_IMAGE_TAG to the previously recorded immutable digest/tag.
docker compose --env-file .env -f infra/compose.yaml up --detach --wait
```

After rollout or rollback, record image digest, migration revision, service
health, and smoke-test evidence in the release checklist.
