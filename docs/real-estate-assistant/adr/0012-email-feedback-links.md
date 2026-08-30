# ADR 0012: Collect feedback through expiring, narrowly scoped email links

- Status: Accepted
- Date: 2026-08-30

## Context

The buyer accepts email links opening a mobile-friendly feedback page. A full
login flow would add friction, but an unauthenticated predictable URL could allow
other people to alter preferences or inspect listing activity.

Email providers and security products may automatically open links to scan them.
Therefore merely loading a URL must never submit feedback or consume the token.

## Decision

Each digest listing receives a cryptographically random token associated with
only:

- one buyer;
- one digest/listing occurrence;
- the feedback-page capability;
- an expiry time, initially 30 days.

Store only a hash of the token in PostgreSQL. The raw token appears only in the
HTTPS link. It contains no readable email address, preference data, or database
identifier.

A `GET` request may display the feedback form but cannot change state or consume
the token. Feedback changes require a deliberate `POST` with CSRF-safe form
handling. After successful submission, mark that token used and show a
confirmation page. If editable feedback is desired later, issue a new scoped
token rather than keeping broad access alive.

## Feedback actions

- strong match/save;
- maybe;
- reject with one or more reason codes plus optional note;
- already seen;
- contacted/viewing arranged;
- never show unchanged listing again;
- for exploration, indicate that the shown trade-off is acceptable.

Repeated feedback may tune soft weights or propose a profile change. It cannot
automatically weaken hard constraints.

## Security and privacy controls

- Use at least 128 bits of cryptographically secure randomness.
- Serve only over HTTPS.
- Scope the endpoint to feedback; tokens cannot access admin pages or other
  listings.
- Avoid recording raw query tokens in application/proxy logs, analytics, or error
  reports.
- Set a strict referrer policy and load no third-party page resources that could
  receive the URL.
- Rate-limit token validation and feedback submission.
- Treat a forwarded email as delegated access until expiry; allow tokens/report
  links to be revoked.
- Visually distinguish private feedback actions from safe-to-share source links
  and provide a separate token-free sharing path.
- Do not rely on obscurity of listing IDs.

## Consequences

- Feedback from a phone requires no password entry.
- Link forwarding creates limited temporary risk, bounded by scope and expiry.
- Scanner-safe two-step GET/form-POST behavior is mandatory.
- The VPS exposes one small HTTPS surface that requires patching and monitoring.
