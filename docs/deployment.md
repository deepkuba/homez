# Deployment

The production topology uses one immutable Homefinder image for migration, web,
workflow-worker, scheduler, and delivery-worker services. PostGIS is reachable
only on the internal backend network. Caddy is the only service with published
ports and forwards only `/feedback/*`; health and administrative routes remain
private. The workflow and delivery workers additionally join a dedicated bridge
network for outbound Gmail, portal redirect-header, and Mailtrap requests. That
network publishes no ports and is not attached to PostGIS or the web service.

Infrastructure changes remain gated by human issue #56, and production secret
entry remains gated by #58. The commands below are a reviewed procedure, not
authorization to alter the VPS, firewall, DNS, or live credentials.

## Host preparation

Create root-owned configuration and secret directories outside the checkout:

```bash
sudo install -d -m 0700 /etc/homez/secrets /etc/homez/config
sudo install -d -m 0700 /var/lib/homez
sudo chown 10001:10001 /var/lib/homez
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
sudo install -m 0600 \
  /secure/input/gmail-token.json /var/lib/homez/gmail-token.json
sudo chown 10001:10001 /var/lib/homez/gmail-token.json
sudo install -m 0400 \
  /secure/input/source-policy.json /etc/homez/config/source-policy.json
sudo chown 10001:10001 /etc/homez/config/source-policy.json
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

## Mailtrap delivery

Homez uses Mailtrap's HTTPS Email API rather than SMTP. For a shadow deployment,
create a Mailtrap Email Sandbox and configure its numeric inbox ID:

```dotenv
HOMEFINDER_MAIL_API_ENDPOINT=https://sandbox.api.mailtrap.io/api/send/123456
HOMEFINDER_MAIL_API_HOST=sandbox.api.mailtrap.io
HOMEFINDER_MAIL_SENDER=homefinder@example.invalid
```

Store the sandbox API token only in `/etc/homez/secrets/mail-api-token`. The
adapter accepts only the exact Mailtrap sandbox endpoint shape or the production
transactional endpoint, checks that the separately configured host matches, and
requires one provider `message_ids` acknowledgement. It sends the stable Homez
delivery identifier as both `Idempotency-Key` and the
`homez_delivery_id` custom variable for traceability.

Production delivery requires a Mailtrap-verified sending domain and:

```dotenv
HOMEFINDER_MAIL_API_ENDPOINT=https://send.api.mailtrap.io/api/send
HOMEFINDER_MAIL_API_HOST=send.api.mailtrap.io
HOMEFINDER_MAIL_SENDER=homefinder@verified.example
```

Mailtrap does not currently document provider-side enforcement of the
`Idempotency-Key` header. A timeout after Mailtrap accepts a message therefore
has an ambiguous duplicate-delivery risk. Sandbox evidence may proceed, but keep
live delivery disabled until that residual risk is explicitly accepted or a
reconciliation mechanism is implemented.

## Shared VPS behind host Caddy

On a VPS where an existing host-level Caddy owns ports 80 and 443, always add
the shared-host override and use a stable project name:

```bash
compose_homez() {
  sudo docker compose --project-name homez \
    --env-file .env \
    -f infra/compose.yaml \
    -f infra/compose.shared-vps.yaml "$@"
}
```

Set `HOMEZ_FRONTEND_SUBNET` and `HOMEZ_TRUSTED_PROXY_IP` in `.env` to the
dedicated frontend bridge subnet and its gateway. Check existing Docker network
subnets first and select a non-overlapping private subnet:

```bash
sudo docker network inspect $(sudo docker network ls --quiet) \
  --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}'
```

The example uses `172.30.138.0/28` with gateway `172.30.138.1`. The gateway is
the only address Uvicorn trusts for forwarded headers. The override places the
bundled ingress behind the inactive `standalone-ingress` profile and publishes
the web service only on `127.0.0.1:18000`.

Render and inspect the merged model before starting a service:

```bash
compose_homez config --quiet
compose_homez config > /tmp/homez-shared-compose.yaml
compose_homez config --services
```

The only published port on a service without a profile must be
`127.0.0.1:18000->8000/tcp` on `web`. Never enable the
`standalone-ingress` profile on the shared VPS. Configure host Caddy to proxy
only `/feedback/*` to `127.0.0.1:18000` and return `404` for all other paths.

During the shadow phase, start an explicit service list so that scheduling and
delivery remain disabled:

```bash
compose_homez up --detach db
compose_homez run --rm migrate
compose_homez up --detach --wait web workflow-worker
```

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
