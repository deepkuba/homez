# Homez MVP deployment plan

Deploy Homez in two stages: a production-like shadow deployment first, then
enable scheduled live delivery only after operational drills and buyer
approval.

## Deployment phases

| Phase | Actions | Exit gate |
|---|---|---|
| 1. Complete readiness | Finish #136: persistent health, scheduled encrypted NAS backups, clean restore, and failure notifications. Resolve or formally waive remaining MVP issues. | CI passes and no unapproved P0 issue remains. |
| 2. Prepare infrastructure | Provision a Linux VPS with Docker Compose, persistent storage, DNS, and firewall. Expose only SSH, HTTP, and HTTPS; PostgreSQL stays private. Create the restricted NAS backup destination. | Infrastructure is authorized under #56 and NAS controls #60/#62. |
| 3. Configure external services | Configure dedicated Gmail OAuth, approved portal policies, Google Routes quota, mail provider, report test inbox, and feedback domain. | Sandbox Gmail and Routes checks pass; quota ceiling and source policies are approved. |
| 4. Install secrets | Create `/etc/homez/secrets`, `/etc/homez/config`, and `/var/lib/homez`. Install database, OAuth, mail, feedback, admin, recipient, and backup secrets as protected files. | Secret files are mode `0600`; Compose rendering and logs reveal no secret values. |
| 5. Select candidate | Use a new immutable `sha-<commit>` image and record its digest, Alembic revision, previous image, and backup recovery point. | The exact commit passes quality, PostgreSQL, and container CI jobs. |
| 6. Deploy in shadow mode | Start PostGIS, migrations, web, workflow worker, and Caddy. Keep the recurring scheduler and delivery worker stopped initially. Use only an approved test inbox for shadow delivery. | Services are healthy, HTTPS works, and private routes remain inaccessible. |
| 7. Prove operations | Run ingestion, workflow, feedback, quota-exhaustion, failure-notification, encrypted-backup, clean-restore, and rollback drills. | Evidence is reviewed for #43, #46-#52, #64, and #66. |
| 8. Approve reports | Generate two production-like shadow reports. Review ranking sections, mobile feedback, safe sharing, recipient rendering, and source accuracy. | Both reports receive buyer approval under #54. |
| 9. Enable live delivery | Record final profile, source, quota, recipient, domain, image, migration, rollback, and recovery-point decisions. Obtain explicit #69 approval, then start the scheduler and delivery worker. | #138 is explicitly approved. |
| 10. Observe and close | Monitor the first live cycle, verifying delivery acknowledgement, workflow backlog, route quota, backups, and alerts. | The first delivery is stable and release evidence is complete. |

## Deployment commands

After preparing `.env` and the protected secret files:

```bash
docker compose --env-file .env -f infra/compose.yaml config --quiet
docker compose --env-file .env -f infra/compose.yaml pull

docker compose --env-file .env -f infra/compose.yaml up --detach db
docker compose --env-file .env -f infra/compose.yaml run --rm migrate
docker compose --env-file .env -f infra/compose.yaml up --detach --wait \
  web workflow-worker ingress
```

After the shadow and release gates pass:

```bash
docker compose --env-file .env -f infra/compose.yaml up --detach --wait \
  scheduler delivery-worker
docker compose --env-file .env -f infra/compose.yaml ps
```

## Required smoke checks

- The public feedback page responds over HTTPS.
- Public `/health` and administrative routes return `404`.
- PostgreSQL has no published host port.
- The migration revision matches the recorded candidate.
- Workflow heartbeats are fresh.
- No secrets, recipient addresses, tokens, or database URLs appear in logs.
- One encrypted backup reaches the NAS and restores into a clean database.
- Rollback uses the recorded immutable digest; workers stop before rollback.

## Release evidence

For every drill, record the timestamp, immutable image digest, migration
revision, and a sanitized result link. Do not record credentials, OAuth
responses, raw email, private feedback links, database dumps, or provider
response bodies.

The current image is a technical baseline, not a live MVP candidate, until
#136 and the human release gates are complete. Refer to
[`docs/deployment.md`](docs/deployment.md),
[`docs/operations.md`](docs/operations.md), and
[`docs/release-checklist.md`](docs/release-checklist.md) for the detailed
procedures and evidence checklist.
