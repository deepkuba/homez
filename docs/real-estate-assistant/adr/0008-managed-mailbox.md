# ADR 0008: Use a managed dedicated mailbox, not a self-hosted mail server

- Status: Proposed
- Date: 2026-08-30

## Context

Portal-provided saved-search alerts are the preferred discovery trigger. The
system needs an inbox for ingestion and a reliable way to send one weekly digest.
The buyer is comfortable using a dedicated mailbox and asks whether Gmail or
self-hosting is preferable.

Self-hosting an email server adds deliverability, reputation, DNS, spam filtering,
security patching, monitoring, backup, and abuse-handling work unrelated to the
home-search product.

## Decision

Use a dedicated managed Gmail account for both alert ingestion and the weekly
digest. The application itself may run locally or on a small hosted service; the
mail server should remain managed.

Use the Gmail API with OAuth 2.0 offline access and the narrowest practical
scopes. Store the refresh token encrypted and separately from application data.
Do not store the Google account password in the application. Use labels to track
source, processing state, parser failure, and completion.

An app password plus IMAP/SMTP is acceptable only as a short-lived prototype
fallback with 2-Step Verification enabled. Google recommends OAuth/Sign in with
Google over app passwords.

## Operational rules

- Use this mailbox only for the property assistant.
- Enable 2-Step Verification and maintain recovery options.
- Process by immutable message ID and make ingestion idempotent.
- Retain the original alert message for parser debugging while respecting a
  defined retention period.
- Quarantine malformed or suspicious messages; never execute email content.
- Allowlist expected sender domains, but do not treat the From header alone as
  proof of authenticity.
- Send digests through the Gmail API from the same dedicated identity.
- Alert the buyer when authorization expires or ingestion stops.

## Consequences

- No mail-server maintenance or deliverability engineering is needed.
- The automation depends on Google availability and OAuth configuration.
- A future provider migration is feasible if mailbox access is isolated behind a
  small interface.
- Gmail's personal-account sending limits are immaterial for one weekly recipient,
  but the design must not expand into bulk mailing without review.

## Current official references

- Gmail API server-side OAuth and offline refresh tokens:
  https://developers.google.com/workspace/gmail/api/auth/web-server
- Gmail guidance for third-party clients:
  https://support.google.com/mail/answer/7126229
- Google app-password guidance:
  https://support.google.com/mail/answer/185833
- Gmail sending limits:
  https://support.google.com/mail/answer/22839
