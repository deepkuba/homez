# Portal email parser contracts

Issue #19 records approval for receiving portal-provided email alerts as the MVP
access method. It does not approve page fetching. The Otodom, Morizon, and Gratka
parsers therefore operate only on supplied email bytes, and the production CLI
does not register them until the source-to-parser governance work in #130 lands.

The current `sanitized-email-v1` contract is backed by the minimal synthetic
fixtures in `data/email_examples/`. It checks the exact fixture sender, source
identity, message size, immutable message ID, date, required listing fields,
allowlisted HTTPS URL shape, and positive PLN/area/room values. A message may
contain multiple listing articles; all are parsed before one atomic catalog
transaction starts. Repeated message IDs reuse every associated listing and
snapshot. Parser versions are stored on accepted and quarantined records, while
quarantine reasons contain only a source/version, stable failure code, and
non-sensitive explanation.

OLX remains blocked because #17 and #18 record that no representative alert is
available. Do not infer its format from another portal or add an OLX parser until
a newly generated minimal fixture passes the fixture-safety scan.

All source policies keep `page_fetch_enabled=False`. Enabling any parser in live
polling requires a reviewed sender configuration and the governance controls in
#130; enabling page retrieval requires a separate explicit approval.
