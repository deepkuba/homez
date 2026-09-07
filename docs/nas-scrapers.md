# NAS portal scrapers

Homez can run OLX, Otodom, Morizon, and Gratka page retrieval as four independent
processes on the NAS. The VPS keeps Gmail ingestion, PostgreSQL, matching, and
report delivery. It sends a validated canonical listing URL to the matching NAS
process and receives bounded structured JSON. PostgreSQL is never exposed to the
NAS.

This feature does not bypass login, CAPTCHA, `403`, or another portal access
control. Confirm that page retrieval is permitted for the intended private use
before setting `page_fetch_enabled` for a source.

Each process makes at most one request at a time, waits 15 seconds plus 0–5
seconds of random jitter between requests, and permits at most 150 requests per
UTC day. A portal `Retry-After` response is respected. Without one, `429` and
`403` pause that source for at least six hours, while `503` starts at five
minutes; repeated failures use exponential backoff. These counters and
cooldowns survive container restarts.

## Network contract

| Source | NAS Tailscale port |
|---|---:|
| OLX | 18101 |
| Otodom | 18102 |
| Morizon | 18103 |
| Gratka | 18104 |

The Compose file binds each port to `HOMEZ_NAS_TAILSCALE_IP`, not `0.0.0.0` or
the NAS LAN address. Add a Tailscale grant allowing only the VPS identity to
reach these four ports on the NAS. Do not forward them on the router.

## Shared secret

Generate one high-entropy token on a trusted machine:

```bash
openssl rand -hex 32
```

Save the value as `scraper-token` in both `/etc/homez/secrets` on the VPS and the
directory configured as `HOMEZ_SCRAPER_SECRETS_DIR` on the NAS. Do not put the
value in `.env`, shell history, Compose YAML, or Git. The container user must be
able to read the file; a typical Linux host uses owner/group `root:10001` and
mode `0440`.

Create one writable state directory per isolated process. On a Linux-like NAS
filesystem, replace the example base path if needed:

```bash
sudo mkdir -p \
  /volume1/Docker/homez/scraper-state/olx \
  /volume1/Docker/homez/scraper-state/otodom \
  /volume1/Docker/homez/scraper-state/morizon \
  /volume1/Docker/homez/scraper-state/gratka
sudo chown -R 10001:10001 /volume1/Docker/homez/scraper-state
sudo chmod 0700 /volume1/Docker/homez/scraper-state/*
```

Set `HOMEZ_SCRAPER_STATE_DIR=/volume1/Docker/homez/scraper-state` in
`.env.nas`. Do not share one state file between processes.

## Start the four NAS processes

Copy the repository `.env.example` to an untracked `.env.nas`, set the immutable
image tag, the NAS Tailscale IPv4 address, and the scraper secret directory, then
run:

```bash
docker compose --project-name homez-scrapers \
  --env-file .env.nas \
  -f infra/compose.nas-scrapers.yaml config --quiet

docker compose --project-name homez-scrapers \
  --env-file .env.nas \
  -f infra/compose.nas-scrapers.yaml up --detach --wait
```

From the VPS, check each private health endpoint over Tailscale. Health does not
require the token and reveals only the fixed source name:

```bash
curl --fail http://100.64.0.10:18101/health
curl --fail http://100.64.0.10:18102/health
curl --fail http://100.64.0.10:18103/health
curl --fail http://100.64.0.10:18104/health
```

Replace the example address with the NAS Tailscale IPv4 address.

## Connect the VPS workflow

Set the four `HOMEFINDER_SCRAPER_*_ENDPOINT` values in the VPS `.env` to the
corresponding NAS Tailscale address and port. Add the scraping overlay to the
existing helper:

```bash
compose_homez() {
  sudo docker compose --project-name homez \
    --env-file .env \
    -f infra/compose.yaml \
    -f infra/compose.shared-vps.yaml \
    -f infra/compose.scraping-vps.yaml "$@"
}
```

For each approved source in `/etc/homez/config/source-policy.json`, add the
literal JSON boolean (not a string):

```json
"page_fetch_enabled": true
```

Validate the merged configuration, pull the same immutable image on both hosts,
and restart only `workflow-worker`. Existing catalog snapshots are normalized
again under `catalog-page-v2`; successful NAS results replace only facts present
on the page, while missing optional facts fall back to the email alert.

## Rollback

Set `page_fetch_enabled` to `false` for every source and restart the VPS workflow
worker. Then stop the NAS project. Email ingestion and existing catalog data
remain available; no schema rollback is required.
