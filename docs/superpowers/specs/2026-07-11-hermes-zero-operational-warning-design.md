# HERMES/Jue Zero Operational Warning Design

Date: 2026-07-11
Status: approved

## 1. Objective

HERMES/Jue must reach and sustain an honest operational `green` state:

- authenticated compact readiness returns `status=green`;
- `blockers=[]` and `warnings=[]`;
- the global operational banner is absent;
- strategy, evidence, and optimization advisories remain visible in their owning
  workspaces;
- no order, fill, live setting, kill switch, or strategy authority changes as a
  side effect of maintenance.

The implementation fixes the production and retention structures that generate
the remaining warnings. It must not rename, suppress, or threshold-shift a real
fault merely to make readiness green.

## 2. Current Evidence

The remaining live warnings are:

1. `runtime_storage_warning`
2. `jue_wiki_repair_queue_growing`

The current `.runtime` footprint is about 5.2 GiB against the 4 GiB warning and
6 GiB risk thresholds. The two dominant avoidable hot-storage groups are:

- `.runtime/jue_wiki/wiki.db`: about 1.34 GiB;
- `.runtime/dryrun`: about 1.33 GiB.

`wiki.db` contains about 3.22 million `wiki_selection_pages` rows. Fewer than
11,000 are included pages; almost all space is rejected-page audit detail. The
table and its primary-key index use about 1.00 GiB together. Historical
consumers use included pages, while rejected rows are audit evidence.

The dry-run directory contains twelve approximately 119 MiB Binance rehearsal
SQLite copies created within one short rehearsal window. They are not open by a
live process. A representative rehearsal database compresses to about 10.5% of
its original size; the current Wiki database compresses to about 8.0%.

The Wiki repair queue has 357 open rows and a 24-hour net growth of 35. The net
growth is primarily:

- `book_depth_gap`: +25;
- `edge_rebuild`: +11;
- requested-symbol summary coverage: +10;
- financial evidence refresh: +8;
- requested-symbol summary resolutions: -19.

These rows combine three different responsibilities: Wiki integrity repair,
evidence/content improvement, and trading-strategy or market-data observations.
Only the first is a global operational concern.

## 3. Chosen Architecture

The approved approach is a lossless hot/cold storage split plus typed repair
lanes. It preserves every archived source row and rehearsal artifact while
keeping the live operational database bounded.

### 3.1 Runtime cold archive

Introduce a `RuntimeColdArchiveV1` service with three operations:

```python
archive(candidate: ArchiveCandidateV1) -> ArchiveResultV1
verify(entry: ArchiveManifestEntryV1) -> ArchiveVerificationV1
restore(entry_id: str, destination: Path) -> RestoreResultV1
```

The default cold root is a sibling of `.runtime`, not a child:

```text
.runtime-cold-archive/
  manifest-v1.json
  dryrun/<scenario>/<entry-id>.tar.gz
  rag-rebuild/<entry-id>.tar.gz
  jue-selection/<yyyy-mm-dd>/<entry-id>.jsonl.gz
```

The root is configurable through an additive read-only setting. Existing API
paths and environment aliases remain unchanged. The default cold root is
excluded from Git and remains on the same monitored filesystem. Disk-free
readiness therefore continues to account for its physical bytes even though the
4/6 GiB hot-runtime thresholds apply only to `.runtime`. If an operator
configures another filesystem, readiness reports and evaluates free space for
both the hot and cold filesystems.

Each manifest entry contains:

- archive version, entry ID, category, logical scenario, and source paths;
- source byte count, file count, row count when applicable, and time range;
- source SHA-256 values;
- compressed archive SHA-256 and byte count;
- creation and verification timestamps;
- restore contract and current lifecycle status.

Archive is always two phase:

1. write a temporary archive;
2. fsync and close it;
3. verify archive checksum, member list, source checksums, and SQLite integrity or
   exported row counts;
4. atomically rename it and atomically update the manifest;
5. only then remove the hot source.

Any failure before step 5 leaves the hot source untouched. A manifest or archive
verification failure is an operational warning; it is never silently ignored.

### 3.2 Dry-run rehearsal lifecycle

Dry-run artifacts are grouped by an explicit logical scenario rather than by
flat filename. Numeric suffixes such as `rehearsal2` through `rehearsal7` are
revisions of the `rehearsal` scenario, not seven unrelated scenarios.

For each completed scenario:

- keep the latest three runs hot for up to 24 hours;
- archive every other completed run immediately after integrity verification;
- archive all runs older than 24 hours, except a manifest-protected hot run;
- retain cold archives indefinitely until a separate approved retention policy
  exists;
- restore a selected run to a caller-provided scratch directory without
  overwriting an existing file.

A rehearsal SQLite bundle is produced from a consistent SQLite backup and its
matching JSON state. WAL/SHM files are not treated as standalone evidence. The
bundle records `PRAGMA integrity_check`, table row counts, and source hashes.

The expired RAG rebuild backup is archived through the same verified bundle
contract. It is not deleted without a verified cold replacement.

### 3.3 Wiki selection audit hot/cold split

Introduce a `JueWikiSelectionAuditStore` boundary. Selection execution and
application code no longer write or query `wiki_selection_pages` directly.

The hot database retains:

- every `wiki_selection_runs` summary;
- every included `wiki_selection_pages` row;
- every rejected page row from the most recent 24 hours;
- archive manifest references and aggregate rejected counts for older runs.

Rejected rows older than 24 hours are exported losslessly to daily gzip JSONL.
Each archive row includes the complete original table payload. Archive entries
record the exact primary-key set, row count, time range, uncompressed stream
hash, and compressed-file hash.

The compactor performs:

1. select a fixed UTC cutoff and primary-key set;
2. export the rows without holding a write transaction;
3. verify the cold archive;
4. publish the manifest entry as `verified_hot_retained`;
5. begin a bounded SQLite write transaction;
6. confirm the same primary-key set and row hashes still exist;
7. delete only the verified keys and commit the SQLite transaction;
8. atomically advance the manifest lifecycle to `hot_removed`.

Rows created after the cutoff cannot enter the deletion set. If the verification
set changes, the transaction rolls back and the archive entry remains staged for
retry.

Historical audit lookup reads hot rows first and then verified cold entries.
Application projection continues to use included hot rows and is unaffected by
rejected-row archiving.

After the initial migration, SQLite is compacted in place with a bounded busy
timeout and pre/post `integrity_check`. The operation is scheduled immediately
after a Wiki cycle. Selection writers use short-lived connections and wait on
the SQLite lock. If compaction cannot acquire the lock, no data is changed and
the next cycle retries. A verified pre-compaction backup remains available until
post-compaction checks pass.

### 3.4 Typed Wiki repair lanes

Add a persisted `repair_lane` contract to every repair action:

```text
integrity  -> broken Wiki schema, source identity, scope isolation, lint, or
              projection contract; owns operational health
evidence   -> missing/weak financials, summaries, coverage, attribution, or
              effectiveness evidence; owns evidence advisories
strategy   -> book depth, edge rebuild, market-data, manager-observation, or
              validation work; owns venue strategy advisories
```

Legacy rows are backfilled by a deterministic action-type registry. Unknown
action types fail closed into `integrity` and surface an explicit classification
warning until registered.

Queue status returns both total and per-lane metrics:

```python
{
    "open_count": int,
    "by_lane": {
        "integrity": RepairLaneStatusV1,
        "evidence": RepairLaneStatusV1,
        "strategy": RepairLaneStatusV1,
    },
    "repair_health": IntegrityRepairHealthV1,
}
```

Global Wiki warnings are derived only from the `integrity` lane. Evidence and
strategy growth remain visible as advisories, action batches, context-pack
inputs, and venue authority constraints. This is not a visual downgrade:
ownership and required response determine classification.

Manager observation rows retain stable scope/action/symbol identity and update
`last_observed_at` plus `observation_count`. They resolve when a later clean
observation exists. They no longer inflate the operational integrity backlog.

### 3.5 Readiness and UI contract

The existing readiness schema stays backward compatible.

- Hot `.runtime` usage below 4 GiB produces no storage warning.
- Hot usage at or above 4 GiB remains a warning; 6 GiB remains a blocker.
- Cold archive size and verification state are included in the disk/storage
  section.
- A corrupt, missing, or unverified archive referenced by the manifest produces
  an operational warning.
- Only integrity repair-lane health contributes Wiki operational warnings.
- Evidence and strategy lanes remain advisories.

The UI continues to render the global banner from blockers and warnings only.
No UI suppression is added. When the producer state is genuinely green, the
existing banner logic naturally renders nothing.

## 4. Migration Sequence

The live migration is serialized and reversible:

1. capture runtime file hashes, SQLite integrity results, KIS/Binance order
   counts, runner PIDs, and readiness signals;
2. create and verify the cold archive root and manifest;
3. archive non-live rehearsal bundles and the expired RAG rebuild backup;
4. archive historical rejected Wiki selection rows without deleting them;
5. verify row-level cold archives;
6. delete only verified rejected-row keys and compact Wiki SQLite;
7. add and backfill repair lanes in a transaction;
8. publish a fresh Wiki ops snapshot;
9. roll source-stale runners one at a time, never restarting KIS and Binance
   execution simultaneously;
10. remeasure storage, readiness, runner health, order counts, and archive
    restorability.

No manager, executor, tick, or order endpoint is invoked for verification.

## 5. Failure Handling

- Archive write, checksum, row-count, or integrity failure: retain hot data and
  return a failed result.
- Manifest atomic-write failure: retain hot data and temporary archive; do not
  delete source.
- Wiki deletion-set mismatch: roll back the transaction and retry from a new
  snapshot.
- SQLite lock timeout: skip compaction and retry after the next Wiki cycle.
- Post-compaction integrity failure: stop rollout, restore the verified backup,
  and preserve all cold archives.
- Unknown repair action type: classify as integrity and emit a classification
  warning.
- Restore collision: fail without overwriting the destination.
- Cold archive corruption discovered later: keep the manifest entry, mark it
  corrupt, and raise an operational warning.

## 6. Testing Strategy

### Unit and contract tests

- archive manifest round-trip, atomic update, checksum failure, and restore;
- no hot deletion before archive verification;
- rehearsal scenario normalization and latest-three hot retention;
- complete SQLite bundle restore with `integrity_check=ok`;
- rejected selection export/import equality by primary key and row hash;
- concurrent rows newer than the cutoff are never deleted;
- included selection pages are never archived or deleted;
- action-type-to-lane registry covers every current action type;
- unknown actions fail closed to integrity;
- strategy/evidence growth cannot generate a global Wiki warning;
- integrity growth, stall, or overdue state still generates warnings.

### Integration tests

- migrate a representative Wiki database and compare selection/application API
  payloads before and after;
- restore a cold rehearsal bundle and compare SQLite table counts and hashes;
- verify readiness reads remain SQLite-write-free;
- verify KIS/Binance authority gates and workspace advisories remain unchanged;
- verify archive corruption produces a warning.

### Live acceptance

The work is complete only when current runtime evidence proves all of the
following:

1. compact and full readiness return `green`, `blockers=[]`, `warnings=[]`;
2. the global operational banner is absent after an authenticated reload;
3. `.runtime` is at most 3.0 GiB, leaving at least 1 GiB headroom to the warning;
4. every cold archive entry verifies and at least one rehearsal plus one Wiki
   selection partition restores successfully;
5. readiness requests produce zero SQLite writes;
6. all enabled runners are alive, source-fresh, and heartbeat-fresh;
7. KIS and Binance order counts are unchanged by migration and verification;
8. KIS/Binance strategy advisories and server-side authority restrictions remain
   present;
9. focused, fast, venue-domain, full, Ruff, project-contract, and diff checks
   pass;
10. no `.env` edit or commit occurs without separate explicit approval.

## 7. Compatibility and Scope

- Existing API routes, environment aliases, kill switches, paper/live defaults,
  and order safety gates are preserved.
- Cold archive support is additive and uses standard-library gzip/tar/SQLite;
  no new runtime dependency is required.
- The change does not expand trading authority or redesign unrelated UI.
- Historical evidence remains losslessly recoverable.
- The design intentionally changes physical retention and operational ownership;
  it does not change the meaning of a real integrity or disk-space fault.
