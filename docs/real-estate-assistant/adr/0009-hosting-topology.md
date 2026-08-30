# ADR 0009: Run on the VPS and back up to the NAS

- Status: Proposed
- Date: 2026-08-30

## Context

The buyer has both an always-on NAS and a VPS. The assistant needs reliable
scheduled ingestion, weekly delivery, persistent listing history, and protected
credentials. It does not initially require a public user-facing service.

## Decision

Run the production application, scheduler, and database in containers on the VPS.
Send encrypted automated backups to the NAS. Keep the NAS off the public
application path.

Initial topology:

- one application container containing ingestion, normalization, ranking, and
  report generation;
- one relational database container;
- cron or a lightweight persistent scheduler for frequent inbox ingestion and
  weekly report generation;
- optional isolated browser worker only for source-reviewed page retrieval;
- encrypted database/config backups copied to the NAS;
- private administration over SSH or a private network/VPN, with no public
  dashboard required for the first version.

## Security and operations

- Default-deny inbound network access; expose no database port publicly.
- Store Gmail OAuth tokens and provider credentials encrypted and outside the
  container image/repository.
- Run containers without root privileges where practical.
- Pin dependencies and browser images; apply security updates deliberately.
- Encrypt backups before they leave the VPS and use a NAS-side restricted account.
- Define retention, for example daily backups for 14 days and weekly backups for
  8 weeks; verify restoration periodically.
- Monitor last successful inbox sync, source health, database backup, and digest
  delivery; send a failure notification through an independent path where
  practical.
- Treat Gmail delivery as independent of routing so a Routes API quota alert can
  still be sent when route enrichment is suspended.

## Consequences

- Scheduled work continues despite home-network or NAS maintenance.
- The NAS holds recoverable data without being exposed as the production host.
- There are two systems to configure, but their responsibilities remain simple.
- A single-user modular monolith avoids distributed-system overhead.
- If privacy or VPS availability becomes more important, the same containers can
  later move to the NAS.
