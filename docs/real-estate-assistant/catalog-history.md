# Catalog history and duplicate review

Slice 3 gives the catalog a stable history independent of any one portal
advertisement.

- A `PropertyCandidate` is keyed by normalized title, location, room count, and
  area. This conservative deterministic key merges matching alert facts across
  sources while avoiding a district/area-only collision.
- Each portal advertisement remains a separate `Listing`; every changed set of
  mutable facts becomes a dated `ListingSnapshot`.
- Pairs that look like duplicates but do not have the exact deterministic key
  are stored as `DuplicateEvidence` with confidence, reasons, and `pending`
  status. Evidence never merges records automatically.
- `merge_candidates` and `split_listing` are explicit operator actions. They
  preserve the underlying listings and snapshots, so a mistaken decision can
  be corrected without losing source history.
- `CandidatePresentation` records the snapshot shown and whether it was
  dismissed. A candidate is eligible for resurfacing after the configured
  cooldown, or immediately when a new snapshot is available.

The duplicate key is intentionally not a claim of legal or physical identity:
coarse alert data can only produce an inference. Buyer review remains required
for fuzzy evidence and false-positive correction.
