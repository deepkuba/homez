# Container delivery

Every push to `main` that passes branch protection builds one application image
and publishes two GHCR tags:

- `main`, a convenient moving tag;
- `sha-<full-commit>`, an immutable deployment/rollback reference.

Configure the GHCR package so the VPS can pull it with a read-only token, copy
`infra/compose.yaml` and `.env.example` to the VPS, and keep the actual `.env`
outside Git. The database has no published port and the current slice publishes
no application port; health checks run inside the Compose network.

## Controlled update

Choose the reviewed image's `sha-*` tag in `.env`, then run:

```bash
docker compose --env-file .env -f compose.yaml pull app
docker compose --env-file .env -f compose.yaml run --rm app alembic upgrade head
docker compose --env-file .env -f compose.yaml up -d --remove-orphans
docker compose --env-file .env -f compose.yaml ps
```

Rollback by restoring the previous `HOMEZ_IMAGE_TAG` and running `pull` plus
`up -d` again. Database migration compatibility must be checked before rollback.

## Why Watchtower is not included

Watchtower requires access to the Docker socket, which is effectively
host-administrator access. Automatically following a mutable tag also makes a
bad rollout immediate and obscures exactly which commit is running. For this
private service, an explicit update to an immutable commit tag is simpler to
audit and roll back. Slice 10 can automate this controlled sequence after VPS
access, backup/restore, health gating, and failure notifications are approved.
