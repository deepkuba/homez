# ADR 0013: Provide token-free sharing and deliver Friday at 10:00

- Status: Accepted
- Date: 2026-08-30

## Context

The buyer wants to forward interesting listings without exposing private feedback
links. The weekly report should arrive Friday at 10:00.

Forwarding the entire personalized digest would also forward still-valid feedback
capabilities. Email-client rendering and copy behavior vary, so visual separation
alone is insufficient.

## Decision

Every listing card has three distinct link classes:

1. **Original listing**: public portal/source URL.
2. **Private feedback**: expiring scoped HTTPS token, visibly labeled private.
3. **Share safely**: a `mailto:` action that opens the buyer's email client with a
   prefilled, token-free subject and body.

The share-safe body includes:

- listing title and original public source URL;
- price, area, locality, and principal facts;
- short reasons it was selected;
- important caveats such as failed exploration filters or unverified legal data;
- no internal IDs, private report URLs, feedback tokens, buyer profile details,
  hidden notes, or inferred sensitive information.

Also include a clearly marked plain-text copy block with no private token. Offer a
variant without any URL for channels where the buyer does not want to share links.
Do not embed private tokens in tracking pixels, images, redirects, or analytics.

For sharing multiple listings, the feedback page may later generate a share-safe
email containing selected cards. Generation requires a valid private token, but
the generated message itself contains no private capability.

## Delivery schedule

Generate and send the digest every Friday at 10:00 in the `Europe/Warsaw` IANA
timezone, honoring daylight-saving changes. Make report generation idempotent by
scheduled period so retries cannot send duplicate emails.

If the scheduler was offline at 10:00, send one catch-up report after recovery and
label it delayed. Do not send a stale catch-up after a configurable cutoff,
initially 24 hours; send an operational failure notification instead.

## Consequences

- The buyer can share from a phone without exposing feedback authority.
- A public listing URL remains shareable while a no-URL copy option is available.
- The email template must clearly separate private and public actions.
- Delivery tests must cover daylight-saving transitions, retries, and delayed
  scheduler recovery.
