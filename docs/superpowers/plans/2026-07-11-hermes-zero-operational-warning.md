# HERMES/Jue Zero Operational Warning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the remaining HERMES/Jue operational warnings honestly by bounding hot runtime storage, preserving every archived artifact, and making only Wiki integrity work own global operational health.

**Architecture:** Add a versioned, checksummed hot/cold archive boundary before changing retention. Route dry-run, expired RAG backup, and old rejected Wiki selection evidence through that boundary. Persist typed Wiki repair lanes, derive global health from the integrity lane only, expose cold-archive verification in readiness, and apply the live migration only after restore and trading-safety invariants pass.

**Tech Stack:** Python 3.10+, SQLite, gzip, tarfile, hashlib, FastAPI, static JavaScript, pytest.

## Global Constraints

- Preserve API paths, environment aliases, kill switches, paper/live defaults, authority gates, and UI readiness semantics.
- Do not edit `.env`, submit manager/executor/tick/order calls, delete unarchived evidence, or commit without separate approval.
- Use only standard-library archive primitives; add no runtime dependency.
- Default all maintenance and migration entry points to dry-run. Require explicit `--apply` for mutation.
- Never remove a hot source until its final archive and manifest entry verify.
- Restore must never overwrite an existing destination.
- Readiness requests remain read-only and perform zero SQLite writes.
- Keep the 4 GiB hot-runtime warning and 6 GiB blocker thresholds unchanged.
- Run KIS/Binance order-count checks before and after live migration; counts must not change.
- Roll runners one at a time and never restart KIS and Binance executors together.

## Task 1: Build the Lossless Runtime Cold Archive Core

**Files:**
- Create: `src/tradecraft/services/runtime_cold_archive.py`
- Create: `tests/test_runtime_cold_archive.py`
- Modify: `.gitignore`

**Interfaces:**

```python
@dataclass(frozen=True)
class ArchiveCandidateV1:
    category: str
    logical_scenario: str
    source_paths: tuple[Path, ...]
    restore_contract: dict[str, Any]
    row_count: int | None = None
    started_at: str | None = None
    ended_at: str | None = None

class RuntimeColdArchiveV1:
    def archive(self, candidate: ArchiveCandidateV1) -> ArchiveResultV1: ...
    def verify(self, entry: ArchiveManifestEntryV1) -> ArchiveVerificationV1: ...
    def restore(self, entry_id: str, destination: Path) -> RestoreResultV1: ...
    def status(self) -> dict[str, Any]: ...
```

- [x] **Step 1: Write failing archive safety tests**

```python
def test_archive_publishes_verified_manifest_before_hot_removal(tmp_path: Path) -> None:
    source = tmp_path / "hot" / "state.json"
    source.parent.mkdir()
    source.write_text('{"ok": true}', encoding="utf-8")
    archive = RuntimeColdArchiveV1(tmp_path / "cold")

    result = archive.archive(ArchiveCandidateV1(
        category="dryrun",
        logical_scenario="rehearsal",
        source_paths=(source,),
        restore_contract={"kind": "files-v1"},
    ))

    assert result.verified is True
    assert source.exists()
    entry = archive.entry(result.entry_id)
    assert entry.lifecycle == "verified_hot_retained"
    assert archive.verify(entry).ok is True


def test_corrupt_archive_never_authorizes_hot_removal(tmp_path: Path) -> None:
    archive, source, entry = _archived_text_fixture(tmp_path)
    entry.archive_path.write_bytes(b"corrupt")

    verification = archive.verify(entry)

    assert verification.ok is False
    assert source.exists()
    assert archive.mark_hot_removed(entry.entry_id, (source,)).removed is False


def test_restore_refuses_destination_collision(tmp_path: Path) -> None:
    archive, _, entry = _archived_text_fixture(tmp_path)
    destination = tmp_path / "restore"
    destination.mkdir()
    (destination / "state.json").write_text("existing", encoding="utf-8")

    result = archive.restore(entry.entry_id, destination)

    assert result.restored is False
    assert result.reason == "destination_collision"
```

- [x] **Step 2: Confirm the new module is missing**

Run:

```bash
pytest tests/test_runtime_cold_archive.py -q
```

Expected: collection fails with `ModuleNotFoundError`.

- [x] **Step 3: Implement manifest-v1 and two-phase publication**

Implement immutable result dataclasses plus:

```python
def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)
```

`archive()` must hash source files, create a temporary deterministic `tar.gz`, fsync it, reopen and verify member hashes, atomically rename it, then publish `manifest-v1.json` with lifecycle `verified_hot_retained`. `mark_hot_removed()` must re-verify immediately before unlink and atomically advance lifecycle to `hot_removed`.

- [x] **Step 4: Implement non-overwriting restore and archive status**

Reject absolute paths, `..` members, symlinks, and any destination collision. Extract to a sibling temporary directory, verify restored hashes, then atomically publish. `status()` returns entry counts, bytes, corrupt/unverified IDs, and `status=ok|warning` without mutating the manifest.

- [x] **Step 5: Ignore the default cold root and run focused tests**

Add `.runtime-cold-archive/` to `.gitignore`, then run:

```bash
pytest tests/test_runtime_cold_archive.py -q
ruff check src/tradecraft/services/runtime_cold_archive.py tests/test_runtime_cold_archive.py
git diff --check -- .gitignore src/tradecraft/services/runtime_cold_archive.py tests/test_runtime_cold_archive.py
```

Expected: all pass; no source is removed by `archive()` alone.

## Task 2: Route Dry-Run and RAG Rebuild Artifacts Through the Archive

**Files:**
- Modify: `src/tradecraft/services/runtime_maintenance.py:21-78,526-610,1710-1960,2118-2280`
- Modify: `src/tradecraft/services/runtime_storage_policy.py:9-72`
- Modify: `src/tradecraft/config.py:245-337`
- Modify: `docs/spec/12_config_env.md`
- Modify: `tests/test_runtime_maintenance.py:69-80,727-829,970-1006`
- Modify: `tests/test_config.py:182-265`

**Interfaces:** `RuntimeStoragePolicy` gains `cold_archive_root`, `archive_dryrun`, `dryrun_hot_hours=24`, `dryrun_hot_per_scenario=3`, and `archive_rag_rebuild_backups`. Cleanup result gains `archive_candidates`, `archived`, `archive_failures`, and `hot_removed`.

- [x] **Step 1: Write failing logical-scenario and dry-run tests**

```python
@pytest.mark.parametrize("name", ["rehearsal", "rehearsal2", "rehearsal7"])
def test_rehearsal_revisions_share_one_logical_scenario(tmp_path: Path, name: str) -> None:
    assert _dryrun_scenario_key(tmp_path / f"binance_{name}.db", tmp_path) == "binance_rehearsal"


def test_cleanup_archives_before_removing_old_dryrun_bundle(tmp_path: Path) -> None:
    policy, database, state = _dryrun_bundle_fixture(tmp_path, age_hours=30)
    result = cleanup_runtime_storage(policy, dry_run=False)
    assert result["archived"][0]["verified"] is True
    assert not database.exists()
    assert not state.exists()
    restored = RuntimeColdArchiveV1(policy.cold_archive_root).restore(
        result["archived"][0]["entry_id"], tmp_path / "restored"
    )
    assert restored.restored is True
    assert _sqlite_integrity(restored.paths[0]) == "ok"
```

- [x] **Step 2: Confirm retention and archive assertions fail**

Run `pytest tests/test_runtime_maintenance.py -k 'dryrun or rag_rebuild' -q`.

Expected: scenario suffixes are treated separately and cleanup has no archive contract.

- [x] **Step 3: Implement consistent SQLite bundles**

Use `sqlite3.Connection.backup()` into a temporary database, run `PRAGMA integrity_check`, record table counts, and bundle the database with matching JSON state. Do not archive standalone `-wal` or `-shm` files. Group numeric rehearsal revisions before keeping the latest three hot for at most 24 hours.

- [x] **Step 4: Replace eligible deletion with verified archive/removal**

Dry-run lists candidates without creating archives. Apply calls `RuntimeColdArchiveV1.archive()`, then `mark_hot_removed()` only when verification is true. Apply the same flow to expired RAG rebuild backups. Protected-manifest paths stay hot.

- [x] **Step 5: Wire additive settings and document defaults**

Add read-only aliases `RUNTIME_COLD_ARCHIVE_ROOT`, `RUNTIME_STORAGE_ARCHIVE_DRYRUN`, `RUNTIME_STORAGE_DRYRUN_HOT_HOURS`, `RUNTIME_STORAGE_DRYRUN_HOT_PER_SCENARIO`, and `RUNTIME_STORAGE_ARCHIVE_RAG_REBUILD_BACKUPS`. Defaults preserve the approved behavior without changing existing aliases.

- [x] **Step 6: Verify maintenance behavior**

```bash
pytest tests/test_runtime_cold_archive.py tests/test_runtime_maintenance.py tests/test_config.py -q
ruff check src/tradecraft/services/runtime_cold_archive.py src/tradecraft/services/runtime_maintenance.py src/tradecraft/services/runtime_storage_policy.py tests/test_runtime_cold_archive.py tests/test_runtime_maintenance.py
```

Expected: pass; corrupt archives retain all hot sources.

## Task 3: Add the Wiki Selection Audit Hot/Cold Boundary

**Files:**
- Create: `src/tradecraft/services/jue_wiki_selection_audit.py`
- Create: `tests/test_jue_wiki_selection_audit.py`
- Modify: `src/tradecraft/services/jue_wiki.py:624-710,11425-11475`
- Modify: `src/tradecraft/services/jue_wiki_application.py:920-955`
- Modify: `tests/test_jue_wiki.py`
- Modify: `tests/test_jue_wiki_selector.py:7820-7865,11210-11270`

**Interfaces:**

```python
class JueWikiSelectionAuditStore:
    def record_run(self, conn: sqlite3.Connection, run: SelectionRunV1) -> None: ...
    def compact_rejected(self, *, cutoff: datetime, apply: bool) -> SelectionCompactionV1: ...
    def included_pages(self, conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]: ...
    def historical_pages(self, run_id: str) -> list[dict[str, Any]]: ...
    def restore_partition(self, entry_id: str, destination: Path) -> RestoreResultV1: ...
```

- [x] **Step 1: Write failing audit-retention tests**

```python
def test_compaction_exports_only_old_rejected_rows(tmp_path: Path) -> None:
    store, old_rejected, included, recent_rejected = _audit_fixture(tmp_path)
    result = store.compact_rejected(cutoff=_utc("2026-07-10T00:00:00Z"), apply=True)
    assert result.exported_keys == [old_rejected.primary_key]
    assert store.hot_row(included.primary_key) is not None
    assert store.hot_row(recent_rejected.primary_key) is not None
    assert store.historical_pages(old_rejected.run_id) == [old_rejected.as_dict()]


def test_rows_inserted_after_fixed_cutoff_are_never_deleted(tmp_path: Path) -> None:
    store, old_row = _single_old_row_fixture(tmp_path)
    result = store.compact_rejected(
        cutoff=_utc("2026-07-10T00:00:00Z"),
        before_delete=lambda: store.insert(_rejected_row("new", selected_at="2026-07-10T00:00:01Z")),
        apply=True,
    )
    assert result.deleted_keys == [old_row.primary_key]
    assert store.hot_row(("new", old_row.page_id)) is not None
```

- [x] **Step 2: Confirm tests fail before the boundary exists**

Run `pytest tests/test_jue_wiki_selection_audit.py -q`.

- [x] **Step 3: Implement lossless daily gzip JSONL export**

Serialize complete rows in stable primary-key order, hash the uncompressed byte stream, gzip to a temporary file, verify decompressed row count/key set/stream hash, and publish a `jue-selection` manifest entry as `verified_hot_retained`.

- [x] **Step 4: Implement verified-key deletion transaction**

Begin `IMMEDIATE` with bounded busy timeout, re-read exactly the exported primary keys, compare row hashes, delete only matching rejected rows older than the fixed cutoff, commit, then advance lifecycle to `hot_removed`. Roll back on any mismatch. Included rows are excluded in both selection and delete predicates.

- [x] **Step 5: Route production writers/readers through the store**

`JueWiki.record_selection_run()` delegates inserts. Application projection calls `included_pages()` and continues to read only included hot rows. Historical audit calls merge hot rows with verified cold partitions and deduplicate by primary key.

- [x] **Step 6: Verify equivalence and safety**

```bash
pytest tests/test_jue_wiki_selection_audit.py tests/test_jue_wiki.py tests/test_jue_wiki_selector.py -q
ruff check src/tradecraft/services/jue_wiki_selection_audit.py src/tradecraft/services/jue_wiki.py src/tradecraft/services/jue_wiki_application.py tests/test_jue_wiki_selection_audit.py
```

Expected: included/application payloads are byte-equivalent before and after rejected-row compaction.

## Task 4: Persist and Enforce Typed Wiki Repair Lanes

**Files:**
- Create: `src/tradecraft/services/jue_wiki_repair_lanes.py`
- Create: `tests/test_jue_wiki_repair_lanes.py`
- Modify: `src/tradecraft/services/jue_wiki.py:920-1160,1495-1590,3055-3165,11450-11515`
- Modify: `src/tradecraft/services/jue_wiki_repair_health.py`
- Modify: `src/tradecraft/api/ops_payloads.py:2177-2515`
- Modify: `tests/test_jue_wiki.py`
- Modify: `tests/test_jue_wiki_repair_health.py`
- Modify: `tests/test_ops_payloads.py`

**Interfaces:** `RepairLane = Literal["integrity", "evidence", "strategy"]`; `classify_repair_action(action_type) -> RepairLaneClassificationV1`; queue payload gains `by_lane`, `unclassified_action_types`, and integrity-only `repair_health` while preserving total fields.

- [x] **Step 1: Write failing registry and signal tests**

```python
def test_every_current_action_type_has_an_explicit_lane() -> None:
    assert unclassified_action_types(current_repair_action_types()) == set()


def test_unknown_action_fails_closed_to_integrity() -> None:
    result = classify_repair_action("future_unknown_action")
    assert result.lane == "integrity"
    assert result.registered is False


def test_strategy_and_evidence_growth_do_not_create_global_warnings() -> None:
    payload = _wiki_ops_payload(integrity=_healthy_lane(), evidence=_growing_lane(), strategy=_growing_lane())
    assert "jue_wiki_repair_queue_growing" not in payload["warnings"]
    assert "jue_wiki_evidence_repair_queue_growing" in payload["advisories"]
    assert "jue_wiki_strategy_repair_queue_growing" in payload["advisories"]
```

- [x] **Step 2: Confirm old all-actions health semantics fail**

Run `pytest tests/test_jue_wiki_repair_lanes.py tests/test_jue_wiki_repair_health.py tests/test_ops_payloads.py -k 'repair_lane or strategy_and_evidence_growth' -q`.

- [x] **Step 3: Implement explicit registry and schema migration**

Create a constant registry covering every literal action type emitted in `jue_wiki.py`. Add `repair_lane TEXT NOT NULL DEFAULT 'integrity'` and `repair_lane_registered INTEGER NOT NULL DEFAULT 1`. Backfill legacy rows in one transaction using the registry; unknowns remain integrity with registered=0.

- [x] **Step 4: Make all write paths persist classification**

`_record_or_refresh_repair_action()` classifies before insert/update. Stable manager-observation identities continue updating `last_observed_at` and `observation_count`; later clean observations resolve the same key.

- [x] **Step 5: Return totals plus per-lane progress**

Build each lane with open/resolved/opened-24h/resolved-24h/net-growth/overdue metrics. Pass only `by_lane["integrity"]` to `evaluate_jue_wiki_repair_health()`. Emit a warning for any unregistered type. Map evidence and strategy health to advisories and preserve action batches/context inputs.

- [x] **Step 6: Verify repair and readiness contracts**

```bash
pytest tests/test_jue_wiki_repair_lanes.py tests/test_jue_wiki_repair_health.py tests/test_jue_wiki.py tests/test_ops_payloads.py tests/test_ops_api_router.py -q
ruff check src/tradecraft/services/jue_wiki_repair_lanes.py src/tradecraft/services/jue_wiki.py src/tradecraft/services/jue_wiki_repair_health.py src/tradecraft/api/ops_payloads.py
```

Expected: real integrity growth still warns; evidence/strategy growth remains actionable but advisory.

## Task 5: Integrate Archive Health, Wiki Compaction, and Readiness

**Files:**
- Modify: `src/tradecraft/runtime/jue_wiki_runner.py`
- Modify: `src/tradecraft/api/ops_payloads.py:496-535,1088-1100`
- Modify: `src/tradecraft/api/ops_readiness.py:77-91`
- Modify: `src/tradecraft/services/runtime_maintenance.py:1710-1960`
- Modify: `tests/test_jue_wiki_runner.py`
- Modify: `tests/test_ops_payloads.py`
- Modify: `tests/test_readiness_performance.py`
- Modify: `tests/test_static_ui.py`

- [x] **Step 1: Write failing archive-readiness tests**

```python
def test_cold_archive_corruption_is_an_operational_warning() -> None:
    payload = build_ops_readiness_payload(provider_status=_provider(cold_archive={"status": "warning", "corrupt_entry_ids": ["a"]}))
    assert "runtime_cold_archive_corrupt" in payload["warnings"]


def test_cold_bytes_do_not_count_toward_hot_runtime_threshold() -> None:
    status = build_runtime_storage_size_status(runtime_dir=Path(".runtime"), size_reader=lambda _: 3 * 1024**3)
    assert status["status"] == "ok"
```

- [x] **Step 2: Confirm the cold archive signal is absent**

Run `pytest tests/test_ops_payloads.py -k 'cold_archive' -q`.

- [x] **Step 3: Publish read-only cold status and disk filesystem details**

The background storage report verifies the manifest and stores cold size/status. Readiness consumes that snapshot only. If hot and cold roots are on different devices, publish free-space metrics for both. Do not alter the hot thresholds.

- [x] **Step 4: Schedule Wiki compaction after a completed cycle**

After the runner publishes selection and outcome state, call audit compaction with a fixed `now - 24h` cutoff. If rows were removed and free space is material, take a verified pre-compaction SQLite backup, set bounded busy timeout, run pre/post integrity checks and `VACUUM`, then publish the updated Wiki/storage snapshots. Lock timeout records a retryable result without changing data.

- [x] **Step 5: Preserve the existing UI behavior**

Add no banner suppression. Keep the static contract that the global banner is hidden exactly when blockers and warnings are empty; venue advisories remain rendered in their workspaces.

- [x] **Step 6: Verify read-only and performance contracts**

```bash
pytest tests/test_jue_wiki_runner.py tests/test_ops_payloads.py tests/test_ops_readiness.py tests/test_ops_api_router.py tests/test_readiness_performance.py tests/test_static_ui.py -q
```

Expected: readiness SQLite writes remain zero; compact warm p95 <=500 ms and full cold p95 <=2 s.

## Task 6: Add a Safe Migration and Restore CLI

**Files:**
- Create: `src/tradecraft/runtime/runtime_archive_cli.py`
- Create: `tests/test_runtime_archive_cli.py`
- Modify: `pyproject.toml:65-105`
- Modify: `docs/spec/12_config_env.md`

**Interface:** `tradecraft-runtime-archive status|migrate|verify|restore`; `migrate` is dry-run unless `--apply`; `restore` requires entry ID and an empty destination.

- [x] **Step 1: Write failing CLI safety tests**

```python
def test_migrate_defaults_to_dry_run(tmp_path: Path) -> None:
    result = runner.invoke(app, ["migrate", "--runtime-dir", str(tmp_path / ".runtime")])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["dry_run"] is True


def test_apply_requires_explicit_flag(tmp_path: Path) -> None:
    result = runner.invoke(app, ["migrate", "--runtime-dir", str(tmp_path / ".runtime"), "--apply"])
    assert json.loads(result.stdout)["apply"] is True
```

- [x] **Step 2: Implement `argparse` commands without a new dependency**

`status` reads only; `migrate` prints planned candidates and only mutates with `--apply`; `verify` checks every manifest entry; `restore` rejects nonempty destinations and prints restored hashes/counts.

- [x] **Step 3: Verify CLI and packaging**

```bash
pytest tests/test_runtime_archive_cli.py -q
tradecraft-runtime-archive --help
ruff check src/tradecraft/runtime/runtime_archive_cli.py tests/test_runtime_archive_cli.py
```

Expected: all commands are discoverable and mutation is opt-in.

## Task 7: Apply the Live Migration and Prove Green State

**Files:**
- Update evidence only: `docs/superpowers/plans/2026-07-10-hermes-continuous-implementation-log.md`
- Runtime mutation: `.runtime/**` and `.runtime-cold-archive/**` through the verified CLI only

- [x] **Step 1: Capture immutable pre-migration evidence**

Record compact/full readiness, `.runtime` bytes, disk-free metrics, `sha256`/mtime for live SQLite files, `PRAGMA integrity_check`, runner PIDs/heartbeats/source freshness, and KIS/Binance order counts. Do not invoke trading endpoints.

- [x] **Step 2: Run a migration dry-run and review every candidate**

```bash
tradecraft-runtime-archive status
tradecraft-runtime-archive migrate
```

Expected: only completed dry-run/RAG artifacts and rejected Wiki rows older than the cutoff are candidates; protected/live files are absent.

- [x] **Step 3: Apply storage migration in reversible order**

```bash
tradecraft-runtime-archive migrate --apply
tradecraft-runtime-archive verify
```

Archive dry-run/RAG first, then Wiki rejected partitions, then verified-key deletion and Wiki compaction. Stop immediately on any failed entry or integrity mismatch.

- [x] **Step 4: Backfill repair lanes and publish fresh snapshots**

Run the transactional schema migration, confirm no unclassified types, complete one Wiki runner cycle, and publish fresh Wiki/storage/readiness snapshots.

- [x] **Step 5: Prove restore before accepting the migration**

Restore at least one rehearsal bundle and one Wiki partition to new scratch directories. Run SQLite integrity/table-count checks and compare Wiki primary keys/row hashes to manifest evidence.

- [x] **Step 6: Roll only source-stale runners and verify each one**

Restart one runner at a time, wait for PID, source fingerprint, and heartbeat freshness before proceeding. Never restart KIS and Binance execution simultaneously.

- [x] **Step 7: Run focused, fast, domain, full, and static verification**

```bash
pytest tests/test_runtime_cold_archive.py tests/test_runtime_maintenance.py tests/test_jue_wiki_selection_audit.py tests/test_jue_wiki_repair_lanes.py tests/test_ops_payloads.py tests/test_readiness_performance.py -q
python scripts/verify.py fast
python scripts/verify.py domain --area binance
python scripts/verify.py domain --area kis
python scripts/verify.py full
ruff check src tests
git diff --check
```

Expected: all pass. If the current `verify.py` interface differs, use the repository-supported equivalent and record the exact command/result.

- [x] **Step 8: Check live acceptance gates**

Accept only when all are true:

```text
compact/full status=green
blockers=[]
warnings=[]
authenticated UI global banner absent
.runtime <= 3.0 GiB
all cold entries verify
readiness SQLite writes=0
enabled runners alive/source-fresh/heartbeat-fresh
KIS order count unchanged
Binance order count unchanged
venue advisories and authority restrictions preserved
```

- [x] **Step 9: Record measured evidence without committing**

Append timestamps, before/after sizes, archive IDs, restore checks, readiness latency/write counts, runner states, order-count invariants, test commands, and remaining risks to the continuous implementation log. Do not stage or commit.

## Plan Self-Review Gate

Before implementation, verify:

```bash
python -c 'from pathlib import Path; t=Path("docs/superpowers/plans/2026-07-11-hermes-zero-operational-warning.md").read_text(); markers=(chr(84)+chr(79)+chr(68)+chr(79), chr(84)+chr(66)+chr(68)); assert not any(marker in t for marker in markers)'
git diff --check -- docs/superpowers/specs/2026-07-11-hermes-zero-operational-warning-design.md docs/superpowers/plans/2026-07-11-hermes-zero-operational-warning.md
```

Expected: no unresolved markers and no whitespace errors. Confirm every design requirement maps to at least one task and live acceptance remains stricter than warning thresholds.
