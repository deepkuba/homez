# Offline benchmark runtime

`homefinder.benchmark.runtime.run_frozen_benchmark` executes an injected immutable
active/candidate parser pair over an authenticated input reader. It verifies the
frozen manifest identity before reading anything, checks each content hash, refuses
expired raw inputs, checks both parser result identities, reports bounded progress,
and holds an exclusive advisory lock throughout the run. All NAS benchmark
invocations must share the same lock path. The lock file must never be removed
while a worker may be running.

The runtime has no transport or persistence imports. The caller must provide a
manifest-scoped authenticated reader and send results only to the existing
`BenchmarkRepository`; it must not write production results. Raw bytes stay in
memory. Parser implementations must be loaded from the authorized immutable
active/candidate builds; result identity checks do not establish build provenance.

A runnable synthetic-fixture rehearsal is available:

```sh
python -m homefinder.benchmark.rehearsal \
  --manifest /fixtures/manifest.json \
  --fixtures /fixtures/content \
  --dependency-lock-file /app/release/requirements.lock \
  --lock-file /runtime/benchmark.lock
```

Serialize a `build_manifest(...)` result with `dataclasses.asdict` and JSON
`default=str`. Each fixture file is named by its SHA-256 content hash. Both release
hashes must equal the installed packaged parser hash. The command rejects raw
artifact entries and a different release pair. It runs the installed parser twice,
prints only progress, and returns nonzero when an input is unavailable. It stores
no results and never creates activation evidence. Input and manifest reads are
bounded; fixture symbolic links are rejected.

Production NAS deployment remains gated on the authorized distinct immutable
image pair and short-lived manifest-scoped artifact identity. No Compose service
is supplied until those inputs are available, as required by ADR 0017 Slice 8.
The eventual service must use one replica, 0.5 CPU, 256 MiB memory, low I/O weight,
positive nice priority, one database connection, a shared lock path and no portal
or proxy credentials. The rehearsal is a repository execution check, not a
substitute for that production benchmark service or human difference review.
