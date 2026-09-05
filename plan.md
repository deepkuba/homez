# Homez MVP deployment plan for a shared VPS

Deploy Homez on the existing VPS behind its existing host-level Caddy reverse
proxy. Homez must not run its bundled Caddy on this host. The public proxy owns
ports 80 and 443; Homez exposes only its web process on a loopback port, while
PostGIS, workers, health, and administrative routes remain private.

Live Friday delivery remains disabled until the production-like shadow phase,
operational drills, and buyer approvals are complete.

## Target architecture

```text
Internet :80/:443
        |
  existing host Caddy
        |-- existing-app.example.com --> existing application
        `-- feedback.example.com ------> 127.0.0.1:18000
                                                |
                                          Homez web:8000
                                                |
                         private Homez Docker backend network
                         |-- PostGIS
                         |-- workflow worker
                         |-- scheduler
                         `-- delivery worker

Homez VPS -- Tailscale/SFTP tcp:2222 --> restricted ASUSTOR destination
```

Different domains, loopback bindings, Compose projects, networks, volumes,
state directories, and secrets separate the applications operationally.
Different public ports alone are not considered an isolation boundary. Use a
separate VPS if either application is untrusted or managed by unrelated
administrators.

## Assumptions

- The existing Caddy service is managed outside the Homez Compose project.
- Caddy already owns public ports 80 and 443 and can obtain a certificate for
  the approved Homez feedback domain.
- `127.0.0.1:18000` is unused; select another loopback port if it is occupied.
- Other explicitly approved public ports used by the existing application may
  remain open. Each must have a documented owner, purpose, authentication, and
  TLS decision.
- The VPS and NAS join the same Tailscale tailnet as `tag:homez-vps` and
  `tag:homez-nas`.

## Deployment phases

| Phase | Actions | Exit gate |
|---|---|---|
| 1. Complete readiness | Finish #136: persistent health, scheduled encrypted NAS backups, clean restore, and failure notifications. Resolve or formally waive remaining MVP issues. | CI passes and no unapproved P0 issue remains. |
| 2. Prepare shared-host integration | Add and test a Compose override that disables the bundled `ingress` service and publishes `web:8000` only as `127.0.0.1:18000`. Configure trusted proxy handling for the existing Caddy boundary. | Architecture tests prove the bundled ingress is inactive, the backend is loopback-only, and forwarded client addresses cannot be spoofed. |
| 3. Prepare infrastructure | Patch the VPS, install supported Docker Engine and Tailscale, reserve resources, inventory existing ports, and create separate Homez directories. | Existing application remains healthy and no port, directory, network, or volume collision exists. |
| 4. Configure external services | Configure dedicated Gmail OAuth, approved portal policies, Google Routes quota, mail provider, test inbox, and feedback domain. Add a Tailscale grant from `tag:homez-vps` to `tag:homez-nas` on TCP 2222. | Gmail, Routes, mail, DNS, and VPS-to-NAS sandbox checks pass. |
| 5. Install secrets | Create `/etc/homez/secrets`, `/etc/homez/config`, and `/var/lib/homez`. Install database, OAuth, mail, feedback, admin, recipient, and backup secrets as protected files. | Secret files are mode `0600`; Compose rendering and logs reveal no secret values. |
| 6. Select candidate | Use a new immutable `sha-<commit>` image and record its digest, Alembic revision, previous image, and backup recovery point. | The exact commit passes quality, PostgreSQL, container, and shared-proxy integration gates. |
| 7. Configure shared Caddy | Add a dedicated feedback-domain site that proxies only `/feedback/*` to `127.0.0.1:18000` and returns `404` for every other path. Validate before reloading Caddy. | Existing application and Homez hostname both pass HTTPS smoke tests without exposing Homez health/admin routes. |
| 8. Deploy in shadow mode | Start Homez PostGIS, migrations, web, and workflow worker under Compose project `homez`. Keep the scheduler and delivery worker stopped. Use only the approved test inbox for deliberate shadow sends. | Containers and heartbeats are healthy; no unintended message is sent. |
| 9. Prove operations | Run ingestion, workflow, feedback, quota-exhaustion, failure-notification, encrypted NAS backup, clean restore, and rollback drills. | Evidence is reviewed for #43, #46-#52, #64, and #66. |
| 10. Approve reports | Generate and review two production-like shadow reports, including mobile feedback and safe sharing. | Both reports receive buyer approval under #54. |
| 11. Enable live delivery | Record final profile, source, quota, recipient, domain, image, migration, rollback, and recovery-point decisions. Obtain explicit #69 approval, then start the scheduler and delivery worker. | #138 is explicitly approved. |
| 12. Observe and close | Monitor the first live cycle, delivery acknowledgement, workflow backlog, route quota, disk/resource pressure, backups, and alerts. | The first delivery is stable and release evidence is complete. |

## Required repository preparation

Before deployment, add a reviewed `infra/compose.shared-vps.yaml` equivalent to:

```yaml
services:
  ingress:
    profiles: ["standalone-ingress"]

  web:
    ports:
      - "127.0.0.1:18000:8000"
```

The override must be covered by architecture tests. Confirm the merged Compose
configuration does not start `ingress` by default and does not bind Homez to
`0.0.0.0`. Configure Uvicorn to trust forwarded headers only from the actual
reverse-proxy boundary, then test that feedback rate limiting distinguishes
clients and does not accept a spoofed `X-Forwarded-For` header from an
untrusted source.

## Existing Caddy configuration

Add a dedicated site block to the existing host Caddyfile:

```caddyfile
feedback.example.com {
    encode zstd gzip

    handle /feedback/* {
        reverse_proxy 127.0.0.1:18000
    }

    handle {
        respond 404
    }
}
```

Replace the example hostname with the approved domain. Validate the complete
shared configuration before reloading it:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl is-active caddy
```

An invalid Caddy change can affect both applications, so retain the last known
good configuration and its rollback command.

## Host and port checks

Before deploying Homez, capture ownership of every listener:

```bash
sudo ss -lntup
sudo docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Networks}}'
```

Record each public port with its protocol, owning application, purpose,
authentication, and TLS decision. Never expose PostgreSQL, Homez `/health`, the
correction API, the Docker daemon, or the loopback upstream publicly.

## Validate the merged Compose configuration

Always use a stable project name and both Compose files:

```bash
compose_homez() {
  sudo docker compose --project-name homez \
    --env-file .env \
    -f infra/compose.yaml \
    -f infra/compose.shared-vps.yaml "$@"
}

compose_homez config --quiet
compose_homez config > /tmp/homez-compose-rendered
```

Review the rendered configuration before pulling anything. It must contain no
secret values or database URL, and its only host binding must be
`127.0.0.1:18000` for Homez web. The bundled `ingress` service must have the
inactive `standalone-ingress` profile.

## Shadow deployment

After preparing `.env`, protected secret files, DNS, and the immutable image:

```bash
compose_homez pull
compose_homez up --detach db
compose_homez run --rm migrate
compose_homez up --detach --wait web workflow-worker
compose_homez ps
```

Do not run `compose_homez up --detach --wait` without a service list during the
shadow phase. Keep `scheduler`, `delivery-worker`, and `ingress` stopped until
their respective gates are satisfied.

## Required smoke checks

- The existing application still behaves normally after the Caddy reload.
- `https://feedback.example.com/feedback/example-report/example-listing`
  responds through the existing Caddy instance.
- Public `/health`, `/corrections/*`, and every non-feedback path return `404`.
- `127.0.0.1:18000` is reachable locally but not through the VPS public IP.
- Homez's bundled `ingress` container is not running.
- PostgreSQL has no published host port.
- The migration revision matches the recorded candidate.
- Workflow heartbeats are fresh.
- Forwarded scheme and client address are correct and cannot be spoofed.
- No secrets, recipient addresses, tokens, or database URLs appear in logs.
- An encrypted backup reaches the NAS over Tailscale and restores into a clean
  database.
- Restarting or rolling back Homez does not restart or alter the other
  application.

## Live activation

After all release gates pass, start only the Homez scheduler and delivery
worker:

```bash
compose_homez up --detach --wait scheduler delivery-worker
compose_homez ps
```

Verify one deliberate test-inbox delivery before changing the protected
recipient file to the approved live recipient. Record the change and explicit
approval; never place the recipient or credentials in Git.

## Rollback

Stop state-changing Homez workers first:

```bash
compose_homez stop workflow-worker scheduler delivery-worker
```

Restore the previous immutable Homez image only when intervening migrations are
backward-compatible. Otherwise restore the recorded encrypted backup into a
clean database. Reload the previous Caddy configuration only if the Homez site
change caused the problem; do not disturb the existing application's site
block. Re-run both applications' smoke tests after rollback.

## Release evidence

For every drill, record the timestamp, immutable image digest, migration
revision, Caddy configuration revision, existing-application smoke result, and
a sanitized result link. Do not record credentials, OAuth responses, raw
email, private feedback links, database dumps, personal addresses, or provider
response bodies.

The current image remains a technical baseline, not a live MVP candidate,
until #136 and the human release gates are complete. Refer to
[`docs/deployment.md`](docs/deployment.md),
[`docs/operations.md`](docs/operations.md), and
[`docs/release-checklist.md`](docs/release-checklist.md) for detailed standalone
procedures and evidence requirements.
