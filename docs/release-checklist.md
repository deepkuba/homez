# MVP release evidence checklist

Live Friday delivery remains disabled until every required automated gate and
human-only gate below has linked evidence. Store only non-sensitive identifiers,
timestamps, hashes, and results. Never attach credentials, raw email, capability
URLs, database dumps, personal addresses, or provider response bodies.

## Candidate identity

- Git commit: `PENDING`
- Immutable image reference and digest: `PENDING`
- Alembic revision: `PENDING`
- Buyer-profile version approved in #27: `PENDING`
- Routing-goal version and quota ceiling approved in #41: `PENDING`
- Source-access decisions: `PENDING`
- Recipient/domain approvals (#33, #44, #45): `PENDING`
- Rollback image and compatible migration range: `PENDING`
- Backup recovery point: `PENDING`

## Automated evidence

| Gate | Required evidence | Status |
|---|---|---|
| Unit, security, fixture PII | `quality` JUnit artifact; zero failures/errors/skips | PENDING |
| PostgreSQL/PostGIS | `postgres-integration` JUnit artifact; all marked tests executed | PENDING |
| Persistent concurrency | quota, workflow claim, delivery claim, and feedback-token tests | PENDING |
| Vertical workflow | sanitized alert → persisted report → test inbox → mobile feedback | PENDING |
| Final image | image build, migration, PostGIS query, runtime client version | PENDING |
| Deployment security | secret-free rendered Compose, private DB/admin/health, HTTPS feedback | PENDING |
| Dependency and configuration | `pip-audit`, Ruff, mypy, yamllint | PENDING |
| Backup and clean restore | #136 final-image drill with representative application read | BLOCKED #136 |
| Failure notification | reviewed test notification | BLOCKED #64 |

The release owner must link the successful GitHub Actions run and artifact IDs.
Any skipped required test invalidates the candidate. A rerun supersedes previous
evidence only when it tests the exact same commit and image digest.

## Production-like evidence

- Test-inbox HTML/plain-text rendering reviewed (#46/#47): `PENDING`
- Mobile feedback GET-without-mutation and deliberate POST (#48/#50): `PENDING`
- Safe-share output reviewed (#52): `PENDING`
- Quota exhaustion and notification drill (#43): `PENDING`
- Application failure notification drill (#64): `PENDING`
- Clean restore witnessed (#66): `BLOCKED #136`
- Shadow report 1 approval: `PENDING`
- Shadow report 2 approval: `PENDING`

Record timestamps, image digest, migration revision, and a sanitized result link
for each drill. Do not paste message contents or private URLs.

## Release decision

Before enabling delivery, verify every other `MVP` issue is closed or has an
owner-approved waiver with residual risk. Confirm firewall/deployment authority
#56, secure secret entry #58, NAS/key controls #60/#62, and explicit live Friday
approval #69. Record the rollback procedure and recovery point, then add the
owner’s approval and timestamp to #138.

Release decision: **NOT APPROVED**
