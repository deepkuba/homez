# Concurrent scrape worker topology (ADR 0017, Slice 2)

The new topology is opt-in and remains inactive. The packaged worker advertises
no executable parser releases and cannot issue portal requests. Enabling the
Compose profile does not activate a parser or enable scraping. Central source
pacing (Slice 3), NAS artifacts and full result policy, executable release registration,
and explicit deployment authorization remain gates before live work.

The VPS overlay is `infra/compose.concurrent-scrapers-vps.yaml`; combine it with
`infra/compose.yaml` and the existing deployment overlays. The independent NAS
model is `infra/compose.concurrent-scrapers-nas.yaml`. Each defines four
source-pinned processes under the `concurrent-scrapers` profile, one for Gratka,
Morizon, Otodom, and OLX. Legacy scraper services remain available until the
separately authorized Slice 16 cutover. The benchmark service belongs to Slice 8
and is not created by these models.

Both deployments use the same `python -m homefinder.scraper.worker` command and
must use the same immutable `HOMEZ_IMAGE@HOMEZ_SCRAPE_IMAGE_DIGEST`. The digest is
required explicitly; do not substitute a mutable tag. Each process runs as user
10001 with 0.5 CPU, 256 MB memory, 128 PIDs, dropped capabilities, a read-only
root filesystem, and a 16 MB private tmpfs for its heartbeat. No raw content,
state directory, database credentials, or host ports are mounted or published.
Health checks read the runtime heartbeat; shutdown has a 30-second grace period.

Each worker has a separate secret file named
`scrape-worker-DEPLOYMENT-SOURCE-token`, under `HOMEZ_SECRETS_DIR` on VPS or
`HOMEZ_SCRAPER_SECRETS_DIR` on NAS. Its matching coordinator credential registry
entry must bind that exact worker ID, deployment, and source. Tokens are read
from mounted files only. Provisioning identities and secret files is a deployment
gate; this repository change creates none.

VPS workers reach `http://web:8000` through a dedicated internal control network.
The web service retains its existing backend/frontend connections; the workers
have no backend network access. NAS workers require
`HOMEZ_SCRAPE_COORDINATOR_URL` to identify an authorized private tailnet endpoint.
Both pools have a separate egress bridge. The public Caddy configuration remains
restricted to `/feedback/*`; exposing the private coordinator through the public
ingress is prohibited. Tailnet routing, grants, and private listener setup remain
explicit infrastructure gates. The overlay does not turn on the coordinator API
or provision its credential registry.

Read-only validation accepts placeholder coordinates and an all-`a` test digest;
these values are synthetic, cannot enable work, and must not be deployed:

```bash
HOMEZ_SCRAPE_IMAGE_DIGEST=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  docker compose --env-file .env.example -f infra/compose.yaml \
  -f infra/compose.concurrent-scrapers-vps.yaml \
  --profile concurrent-scrapers config --quiet

HOMEZ_SCRAPE_IMAGE_DIGEST=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
HOMEZ_SCRAPE_COORDINATOR_URL=http://100.64.0.1:18000 \
  docker compose --env-file .env.example \
  -f infra/compose.concurrent-scrapers-nas.yaml \
  --profile concurrent-scrapers config --quiet
```

The topology tests verify source pinning, independent identities, database
isolation, resource limits, inactive profiles, heartbeat commands, and the lack
of persistence mounts. Tests and validation do not contact portals or Webshare.

## Runtime and normalization handoff

The worker checks its credential-bound configured identity before claiming,
advertises only supplied executable release hashes, and rejects foreign,
unsupported, replay, or too-short leases before fetching. A separate heartbeat
thread renews leases during fetch/parse/acknowledgement; shutdown stops claims,
finishes the bounded in-flight work, and marks worker health unavailable.

`BoundedPageTransport` requires an injected connector. It validates source URLs,
bounds both transferred and post-decompression bytes to 2 MB, supports identity,
gzip, and deflate, and rejects redirects and invalid/incomplete compressed
streams. It always closes responses and keeps bytes in memory. The shipped
command uses `DisabledPageTransport` and an empty executable-release set; it
cannot construct a portal connection or start live work in this slice.
`PortalPageScraper.parse_bytes` separates the legacy response parser from fetch
without registering the generic parser as a new production release.

The private `POST /internal/scrape/v1/complete` endpoint accepts at most 128 KB
of typed production metadata/results, never a raw-response field. Its transaction
fences the lease and activation epoch, stores an immutable page capture and
production-only parser result/candidates, and completes the task together.
Identical acknowledgement retries return success without another capture.
Retention deadlines use the capture's `fetched_at`; expired results are excluded
from reads even before the later purge job exists. Detailed field resolution and
release lifecycle/revocation checks still belong to Slices 5 and 7.

When concurrent scraping is enabled for a reviewed source, normalization enqueues
one live task and releases its workflow lease while awaiting the result.
Accepted partial results resume the existing fact/enrichment/report path. A
captured parser failure returns explicit unknowns and `artifact-unavailable`
until Slice 4 supplies NAS artifacts. No parser failure alone requests another
download. Legacy mode remains the default.

Acknowledgement failures retry the same metadata three times within the lease.
A worker crash or coordinator outage lasting beyond the lease remains an
ambiguous network outcome: lease recovery may perform a new request. Fencing
guarantees one accepted task result, not exactly-once external HTTP delivery.
The fake concurrency test proves no duplicates for healthy concurrent workers.
No production reliability claim replaces the later outage drills.

Production gates: reviewed central pacing/denial routing (Slice 3), NAS artifact
service (Slice 4), complete result/release policy (Slices 5–7), immutable deployed
parser builds, per-worker secrets, private tailnet routing, and explicit human
deployment/activation approval. This slice releases no network recovery batch.
