# Portal email parser contracts

Issue #19 records approval for receiving portal-provided email alerts as the MVP
access method. The Otodom, Morizon, Gratka, and OLX email parsers continue to
operate only on supplied email bytes. Page retrieval is a separate, explicitly
enabled adapter hosted on the buyer's NAS; see
[`nas-scrapers.md`](../nas-scrapers.md).

The `portal-email-v3` contract is backed by minimal synthetic fixtures in
`data/email_examples/`. Source identity comes from the source-specific sender
allowlist; the parser does not trust a private email header. Each parser checks
message size, immutable message ID, date, required listing fields, direct
allowlisted HTTPS listing hosts, and positive PLN/area/room values. Multiple
configured senders and listing hosts are supported. Query strings and fragments
are removed from accepted listing URLs before storage.

The parser first recognizes the explicit synthetic contract and then supports a
conservative generic email-card shape: an allowlisted listing link inside an
`article`, list item, table row/cell, or `div`, with nearby title, location,
price, area, and room text. A message may contain multiple listing cards; all are
parsed before one atomic catalog transaction starts. Repeated message IDs reuse
every associated listing and snapshot. Parser versions are stored on accepted
and quarantined records, while quarantine reasons contain only a source/version,
stable failure code, and non-sensitive explanation.

`olx_alert.eml` is a minimal synthetic example derived from an owner-supplied
OLX saved-search message. Sensitive delivery/authentication headers, mailbox
addresses, active image and click URLs, tracking identifiers, and listing
content from the source export were not retained. The example records only the
reviewable multipart/table alert shape. It is parsed by the dedicated OLX parser
and registered in the production CLI.

Portal tracking links are normalized without ever treating a tracking host as a
listing host. Morizon and Gratka destinations are decoded locally from the
bounded Base64URL path segment. OLX and Otodom use a `HEAD` request to their exact
hard-coded tracking host, do not follow the response, require one HTTP 302 and
one `Location`, then validate that destination against the configured direct
listing hosts and portal-specific offer path. Query strings and fragments are
discarded. Redirect results are cached by a SHA-256 digest so personalized URLs
are not retained as cache keys. Generic extraction resolves only links already
surrounded by price, area, and room data, avoiding navigation and preference
links. Resolution can register a click in portal analytics.

The Otodom, Morizon, and Gratka examples are also synthetic and do not justify
fragile portal-specific CSS selectors. Before enabling live polling, collect at
least two newly generated sanitized alerts per portal (including a multi-listing
message and a representative layout variant) and verify that direct listing
URLs plus the required numeric and location fields survive sanitization. Never
add click-tracking hosts to `allowed_hosts` to make a fixture pass.

Source policies default to `page_fetch_enabled=False`: redirect normalization
reads headers but never fetches a listing page. When the buyer explicitly enables
the boolean for a source, the VPS sends only its validated canonical listing URL
to that source's authenticated NAS process. The page scraper accepts one exact
portal host/path contract, bounds response size and time, does not follow
redirects, and prefers Schema.org JSON-LD. Missing optional fields retain values
from the email alert. A blocked or malformed response is treated as a retryable
workflow failure; the scraper does not evade access controls.

Enabling live polling still requires a reviewed sender configuration and the
governance controls in #130. Page fetching additionally requires a source-specific
terms review and the NAS/Tailscale controls described in the runbook.
