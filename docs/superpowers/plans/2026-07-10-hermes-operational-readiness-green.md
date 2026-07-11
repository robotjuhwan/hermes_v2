# HERMES Jue Operational Readiness Green Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make HERMES/Jue operational readiness honestly green, hide the global operations banner when only strategy advisories remain, and keep all trading safety restrictions intact.

**Architecture:** Publish full and compact readiness snapshots from a background operations producer, then make API requests read only those snapshots. Classify operational faults separately from venue strategy advisories, supervise report collection across a killable child-process boundary, enforce typed prompt budgets, evaluate Wiki repair health by progress/deadlines, and recover stale runners one at a time with verification.

**Tech Stack:** Python 3.10+, FastAPI, asyncio, SQLite, static JavaScript/CSS, pytest, tmux-managed runners.

## Global Constraints

- Preserve existing API paths, environment-variable aliases, kill switches, and paper/live defaults.
- Do not change `.env`, live trading settings, or strategy authority.
- Do not delete runtime data or audit history.
- Do not submit a manager run, executor tick, or order during verification.
- Do not commit; the user requires separate approval for commits.
- Readiness requests must perform zero SQLite and filesystem writes.
- Compact readiness warm p95 must be at most 500 ms; full readiness cold p95 must be at most 2 seconds.
- Strategy advisories remain visible in KIS/Binance workspaces and continue to restrict scaling.
- Work on at most one structural change and one independent feature at a time.

## Current Baseline

- `status=yellow`, `blockers=[]`.
- Warnings: `restart_required`, `reports_db_stale`, `llm_prompt_payload_large`, and five Jue Wiki queue/coverage warnings.
- Strategy advisories: two KIS validation advisories and three Binance validation advisories.
- Stale-source runners: `control`, `naver_reports`, `kis_block_trader`, `investment_memory`, `live_evaluator`, `jue_wiki`.
- The Naver Reports process is alive but its last logged collection never completed.
- Market Judge recent prompt input is about 352k–356k characters.
- Jue Wiki has 346 open repairs and 2,907 resolved repairs; open work alone currently creates warnings.

## File Structure

- Create `src/tradecraft/services/ops_readiness_snapshot.py`: versioned full/compact snapshot coordinator; refresh is the only write path.
- Create `src/tradecraft/services/jue_wiki_repair_health.py`: pure repair progress/deadline classifier.
- Create `src/tradecraft/services/market_judge_prompt.py`: Market Judge typed prompt compaction and budget contract.
- Create `src/tradecraft/runtime/naver_reports_worker.py`: one report cycle in a killable child process.
- Create `src/tradecraft/runtime/runner_recovery.py`: verified sequential runner recovery.
- Create `tests/test_ops_readiness_snapshot.py`, `tests/test_jue_wiki_repair_health.py`, `tests/test_market_judge_prompt.py`, and `tests/test_runner_recovery.py`.
- Modify `src/tradecraft/api/ops_payloads.py`: operational/advisory signal split and Wiki signal integration.
- Modify `src/tradecraft/api/ops_readiness.py`: carry the new signal/action fields into compact/full payloads.
- Modify `src/tradecraft/api/ops.py`: serve already-published full/compact snapshots without request-time projection.
- Modify `src/tradecraft/main.py`: start/stop the background snapshot producer and retain a fresh builder only for recovery actions.
- Modify `src/tradecraft/config.py` and `docs/spec/12_config_env.md`: document additive snapshot, Wiki health, and Market Judge budget settings.
- Modify `src/tradecraft/services/jue_wiki.py`: queue timing metrics, open-work deduplication, and atomic equivalent-resolution.
- Modify `src/tradecraft/runtime/jue_wiki_runner.py`: publish queue health after repair finalization.
- Modify `src/tradecraft/services/intelligence.py` and `src/tradecraft/runtime/naver_reports_runner.py`: stage progress and child supervision.
- Modify `src/tradecraft/runtime/watchdog_runner.py`: evaluate the actual `naver_reports` state heartbeat.
- Modify `src/tradecraft/services/market_judgment.py` and `src/tradecraft/runtime/market_judge_runner.py`: enforce the Market Judge runtime prompt contract before the LLM call.
- Modify `src/tradecraft/services/kis_manager_prompt.py`: keep KIS core types while reducing large optional evidence below the warning budget.
- Modify `src/tradecraft/api/llm_payloads.py`: require a healthy recovery window or a verified process restart before clearing a large-prompt warning.
- Modify `src/tradecraft/runtime/process_status.py`: schedule verified rolling recovery instead of unverified multi-runner replacement.
- Modify `src/tradecraft/web/static/app.js`: global banner renders operational signals only.
- Modify focused tests in `tests/test_ops_payloads.py`, `tests/test_ops_api_router.py`, `tests/test_readiness_performance.py`, `tests/test_static_ui.py`, `tests/test_jue_wiki.py`, `tests/test_naver_reports_runner.py`, `tests/test_watchdog_runner.py`, `tests/test_market_judgment.py`, `tests/test_kis_manager_prompt.py`, `tests/test_llm_payloads.py`, and `tests/test_process_status.py`.

---

### Task 1: Separate Global Operational Signals from Strategy Advisories

**Files:**
- Modify: `src/tradecraft/api/ops_payloads.py:1146-1229, 2427-2520`
- Modify: `src/tradecraft/api/ops_readiness.py:473-542`
- Modify: `src/tradecraft/api/ops.py:20-180`
- Modify: `src/tradecraft/web/static/app.js:642-710`
- Test: `tests/test_ops_payloads.py:1543-1650, 2620-2670`
- Test: `tests/test_static_ui.py:2869-3025`

**Interfaces:**
- Consumes: existing `blockers`, `warnings`, `advisories`, and `remediation_actions` lists.
- Produces: `operational_remediation_actions: list[dict[str, Any]]` and `advisory_actions: list[dict[str, Any]]`; preserves legacy `remediation_actions` as their stable concatenation.

- [ ] **Step 1: Write failing server signal-contract tests**

```python
def test_finalize_ops_readiness_splits_operational_and_advisory_actions() -> None:
    summary = finalize_ops_readiness_signals(
        environment_signals={"blockers": [], "warnings": ["restart_required"]},
        trading_validation_status={
            "summary": {"readiness": "probe", "diagnostic_fail_count": 1}
        },
        runner_liveness={"warnings": [], "stale_processes": ["jue_wiki"]},
        llm_operational={"critical": {}},
        semantic_checks={"warnings": []},
    )

    assert summary["status"] == "yellow"
    assert {row["id"] for row in summary["operational_remediation_actions"]} == {
        "restart_stale_runners"
    }
    assert "review_trading_validation_diagnostics" in {
        row["id"] for row in summary["advisory_actions"]
    }
    assert summary["remediation_actions"] == [
        *summary["operational_remediation_actions"],
        *summary["advisory_actions"],
    ]
```

- [ ] **Step 2: Write failing global-banner tests**

```python
def test_ops_banner_hides_when_only_strategy_advisories_remain() -> None:
    body = _js()[
        _js().index("function renderOpsBanner"):
        _js().index("function metricTone")
    ]
    assert (
        'banner.hidden = blockers.length === 0 && warnings.length === 0;'
        in body
    )
    assert "hasOnlyAdvisories" not in body
    assert "쥬 운영 정상 · 전략 개선 큐" not in body


def test_ops_banner_uses_only_operational_remediation_actions() -> None:
    body = _js()[
        _js().index("function renderOpsBanner"):
        _js().index("function metricTone")
    ]
    assert "readiness.operational_remediation_actions" in body
    assert "renderTradingValidationBottleneckSummary" not in body
    assert "renderOpsAdvisoryDetails" not in body
```

- [ ] **Step 3: Run the new tests and confirm the old contract fails**

Run:

```bash
pytest tests/test_ops_payloads.py::test_finalize_ops_readiness_splits_operational_and_advisory_actions tests/test_static_ui.py::test_ops_banner_hides_when_only_strategy_advisories_remain tests/test_static_ui.py::test_ops_banner_uses_only_operational_remediation_actions -q
```

Expected: FAIL because the split action fields do not exist and the banner still includes advisories.

- [ ] **Step 4: Implement the split action contract**

Use one helper in `ops_payloads.py` so `finalize_ops_readiness_signals()` and `merge_section_readiness_signals()` cannot diverge:

```python
def _split_readiness_actions(
    *,
    blockers: list[str],
    warnings: list[str],
    advisories: list[str],
    stale_processes: list[str],
    missing_processes: list[str],
    duplicate_processes: list[str],
) -> dict[str, list[dict[str, Any]]]:
    operational = build_ops_remediation_actions(
        blockers=blockers,
        warnings=warnings,
        stale_processes=stale_processes,
        missing_processes=missing_processes,
        duplicate_processes=duplicate_processes,
    )
    advisory = build_ops_remediation_actions(
        blockers=[],
        warnings=advisories,
        stale_processes=[],
        missing_processes=[],
        duplicate_processes=[],
    )
    return {
        "operational_remediation_actions": operational,
        "advisory_actions": advisory,
        "remediation_actions": [*operational, *advisory],
    }
```

Carry all three fields through `build_ops_readiness_payload()` and the compact key sets without removing legacy fields.

- [ ] **Step 5: Implement operational-only banner rendering**

```javascript
const warnings = Array.isArray(readiness.warnings) ? readiness.warnings : [];
const blockers = Array.isArray(readiness.blockers) ? readiness.blockers : [];
const operationalActions = Array.isArray(readiness.operational_remediation_actions)
  ? readiness.operational_remediation_actions
  : [];
banner.hidden = blockers.length === 0 && warnings.length === 0;
if (banner.hidden) {
  banner.innerHTML = "";
  return;
}
const remediationHtml = renderOpsRemediationActions(operationalActions, 3);
```

Do not render validation bottlenecks or advisory details in `renderOpsBanner()`. Keep them in the existing KIS/Binance workspace renderer.

- [ ] **Step 6: Run server and static UI contract tests**

Run:

```bash
pytest tests/test_ops_payloads.py tests/test_ops_api_router.py tests/test_static_ui.py -q
```

Expected: PASS after replacing the two old tests that explicitly required an advisory-only global banner.

- [ ] **Step 7: Record a no-commit checkpoint**

Run:

```bash
git diff --check -- src/tradecraft/api/ops_payloads.py src/tradecraft/api/ops_readiness.py src/tradecraft/api/ops.py src/tradecraft/web/static/app.js tests/test_ops_payloads.py tests/test_ops_api_router.py tests/test_static_ui.py
```

Expected: exit 0. Do not stage or commit.

### Task 2: Add a Versioned Full/Compact Readiness Snapshot Coordinator

**Files:**
- Create: `src/tradecraft/services/ops_readiness_snapshot.py`
- Create: `tests/test_ops_readiness_snapshot.py`
- Modify: `src/tradecraft/services/ops_section_snapshot.py:10-85`

**Interfaces:**
- Consumes: `builder: Callable[[], dict[str, Any]]`, `compact_builder: Callable[[dict[str, Any]], dict[str, Any]]`, and `RuntimeStateStore`.
- Produces: `OpsReadinessSnapshotCoordinator.refresh()`, `.current_full()`, `.current_compact()`, and `.run(stop_event)`.

- [ ] **Step 1: Write failing coordinator tests**

```python
def test_current_reads_published_payload_without_calling_builder(tmp_path: Path) -> None:
    calls = 0

    def builder() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"status": "green", "warnings": [], "blockers": []}

    coordinator = OpsReadinessSnapshotCoordinator(
        builder=builder,
        compact_builder=lambda payload: {"compact": True, **payload},
        config=OpsReadinessSnapshotConfig(path=tmp_path / "ops.json"),
    )
    coordinator.refresh()
    calls_after_refresh = calls

    assert coordinator.current_full()["status"] == "green"
    assert coordinator.current_compact()["compact"] is True
    assert calls == calls_after_refresh


def test_missing_snapshot_returns_bounded_warning_without_refresh(tmp_path: Path) -> None:
    coordinator = OpsReadinessSnapshotCoordinator(
        builder=lambda: (_ for _ in ()).throw(AssertionError("must not refresh")),
        compact_builder=lambda payload: payload,
        config=OpsReadinessSnapshotConfig(path=tmp_path / "missing.json"),
    )

    assert coordinator.current_full() == {
        "status": "yellow",
        "blockers": [],
        "warnings": ["ops_readiness_snapshot_missing"],
        "advisories": [],
        "snapshot": {"status": "missing"},
    }


def test_failed_refresh_keeps_last_known_good_snapshot(tmp_path: Path) -> None:
    responses = iter([
        {"status": "green", "warnings": [], "blockers": []},
        RuntimeError("provider failed"),
    ])

    def builder() -> dict[str, Any]:
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    coordinator = OpsReadinessSnapshotCoordinator(
        builder=builder,
        compact_builder=lambda payload: {"compact": True, **payload},
        config=OpsReadinessSnapshotConfig(path=tmp_path / "ops.json"),
    )
    coordinator.refresh()
    coordinator.refresh()

    assert coordinator.current_full()["status"] == "green"
    assert coordinator.status()["last_refresh_error"] == "provider failed"
```

- [ ] **Step 2: Run the coordinator tests and confirm import failure**

Run:

```bash
pytest tests/test_ops_readiness_snapshot.py -q
```

Expected: collection ERROR because the module is not defined.

- [ ] **Step 3: Implement the snapshot data types and atomic persistence**

```python
OPS_READINESS_SNAPSHOT_VERSION = "ops_readiness_snapshot_v1"


@dataclass(frozen=True)
class OpsReadinessSnapshotConfig:
    path: Path
    refresh_interval_sec: float = 15.0
    max_age_sec: float = 60.0


@dataclass(frozen=True)
class PublishedOpsReadinessV1:
    generated_at: str
    source_at: str
    fresh_until: str
    full: dict[str, Any]
    compact: dict[str, Any]
    version: str = OPS_READINESS_SNAPSHOT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

Use `RuntimeStateStore.write_snapshot()` only from a successful `refresh()`. `current_full()` and `current_compact()` may read memory or the existing file but may not call `builder`, compact, or write. A refresh exception records in-memory coordinator status and retains the last-known-good file/payload; it must not overwrite it with an error snapshot.

- [ ] **Step 4: Implement stale and corrupt snapshot behavior**

```python
def _unavailable_payload(reason: str) -> dict[str, Any]:
    return {
        "status": "yellow",
        "blockers": [],
        "warnings": [f"ops_readiness_snapshot_{reason}"],
        "advisories": [],
        "snapshot": {"status": reason},
    }
```

For an expired snapshot, return its last-known payload with `ops_readiness_snapshot_stale` appended and `snapshot.status="stale"`. Never recompute from the read method.

- [ ] **Step 5: Run the snapshot unit tests**

Run:

```bash
pytest tests/test_ops_readiness_snapshot.py tests/test_runtime_test_isolation.py -q
```

Expected: PASS and no file outside `tmp_path` changes.

- [ ] **Step 6: Record a no-commit checkpoint**

Run `git diff --check -- src/tradecraft/services/ops_readiness_snapshot.py src/tradecraft/services/ops_section_snapshot.py tests/test_ops_readiness_snapshot.py`.

Expected: exit 0. Do not stage or commit.

### Task 3: Move Readiness Projection Out of the Request Path

**Files:**
- Modify: `src/tradecraft/config.py:170-190`
- Modify: `docs/spec/12_config_env.md`
- Modify: `src/tradecraft/main.py:1240-1280, 2676-2860, 3830-3850`
- Modify: `src/tradecraft/api/ops.py:780-810`
- Test: `tests/test_config.py`
- Test: `tests/test_ops_api_router.py:764-840`
- Test: `tests/test_readiness_performance.py`

**Interfaces:**
- Consumes: `OpsReadinessSnapshotCoordinator` from Task 2 and the existing fresh `_build_ops_readiness()` projection.
- Produces: `_published_ops_readiness()`, `_published_ops_readiness_compact()`, and a lifespan-owned background refresh task.

- [ ] **Step 1: Add failing request-path and write-isolation tests**

```python
def test_full_and_compact_routes_only_read_published_snapshots() -> None:
    full_calls = 0
    compact_calls = 0

    def full() -> dict[str, Any]:
        nonlocal full_calls
        full_calls += 1
        return {"status": "green"}

    def compact() -> dict[str, Any]:
        nonlocal compact_calls
        compact_calls += 1
        return {"compact": True, "status": "green"}

    app = FastAPI()
    app.include_router(build_ops_router(OpsRouteDeps(
        require_admin_auth=lambda: None,
        build_ops_readiness=full,
        build_compact_ops_readiness=compact,
        build_codex_native_status=lambda: {},
        refresh_codex_native_checks=lambda force=False: None,
        system_metrics_snapshot=lambda: {},
        watchdog_status=lambda: {},
        restart_runner_processes=lambda keys, delay_sec=0.5: {"keys": keys},
        build_settings_catalog=lambda: {},
        update_settings_env=lambda updates, confirm_high_risk=False: {},
    )))
    client = TestClient(app)
    assert client.get("/api/ops/readiness").json()["status"] == "green"
    assert client.get("/api/ops/readiness?compact=true").json()["compact"] is True
    assert (full_calls, compact_calls) == (1, 1)
```

At the coordinator level, snapshot the bytes and `st_mtime_ns` of all configured SQLite files, call both read methods 20 times, and assert every value is unchanged.

- [ ] **Step 2: Run the tests and confirm the live providers are still called**

Run:

```bash
pytest tests/test_ops_api_router.py::test_full_and_compact_routes_only_read_published_snapshots tests/test_readiness_performance.py -q
```

Expected: FAIL until route dependencies point to published snapshots.

- [ ] **Step 3: Add additive snapshot settings**

```python
ops_readiness_snapshot_path: str = Field(
    default=".runtime/ops_readiness_snapshot.json",
    alias="TRADECRAFT_OPS_READINESS_SNAPSHOT_PATH",
)
ops_readiness_refresh_interval_sec: float = Field(
    default=15.0,
    alias="TRADECRAFT_OPS_READINESS_REFRESH_INTERVAL_SEC",
)
ops_readiness_snapshot_max_age_sec: float = Field(
    default=60.0,
    alias="TRADECRAFT_OPS_READINESS_SNAPSHOT_MAX_AGE_SEC",
)
```

Document all three names in the Runtime/Storage config table. Do not alter any existing alias.

- [ ] **Step 4: Wire the producer into FastAPI lifespan**

```python
ops_readiness_refresh_task: asyncio.Task[None] | None = None


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    global ops_readiness_refresh_task
    coordinator = _ops_readiness_snapshot_coordinator()
    await asyncio.to_thread(coordinator.refresh)
    stop_event = asyncio.Event()
    ops_readiness_refresh_task = asyncio.create_task(coordinator.run(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        if ops_readiness_refresh_task is not None:
            await ops_readiness_refresh_task
            ops_readiness_refresh_task = None
```

Merge this with the existing Telegram lifecycle instead of replacing it.

- [ ] **Step 5: Serve snapshots and preserve a fresh recovery builder**

Use these route dependencies:

```python
build_ops_readiness=lambda: _ops_readiness_snapshot_coordinator().current_full(),
build_compact_ops_readiness=(
    lambda: _ops_readiness_snapshot_coordinator().current_compact()
),
build_ops_restart_readiness=lambda: _build_ops_readiness(),
```

Delete the request-time cache selection from the readiness route path after confirming no other caller depends on it. Keep a fresh builder for `/api/ops/restart` safety confirmation.

- [ ] **Step 6: Make the compact snapshot direct**

During `refresh()`, compute and persist compact output once:

```python
full = dict(self._builder())
compact = dict(self._compact_builder(full))
compact["compact"] = True
now = self._now()
generated_at = now.isoformat()
self._publish(PublishedOpsReadinessV1(
    generated_at=generated_at,
    source_at=str(full.get("checked_at") or generated_at),
    fresh_until=(
        now + timedelta(seconds=self.config.max_age_sec)
    ).isoformat(),
    full=full,
    compact=compact,
))
```

The request handler returns `current_compact()` directly. It must not call `_compact_ops_readiness()` or build the full payload.

- [ ] **Step 7: Run performance and API tests**

Run:

```bash
pytest tests/test_ops_readiness_snapshot.py tests/test_ops_api_router.py tests/test_readiness_performance.py tests/test_config.py -q
```

Expected: PASS; 20 warm compact reads p95 <= 0.5s and full snapshot read <= 2s.

- [ ] **Step 8: Record a no-commit checkpoint**

Run `git diff --check -- src/tradecraft/main.py src/tradecraft/config.py docs/spec/12_config_env.md src/tradecraft/api/ops.py tests/test_config.py tests/test_ops_api_router.py tests/test_readiness_performance.py`.

Expected: exit 0. Do not stage or commit.

### Task 4: Classify Jue Wiki Repairs by Progress and Deadlines

**Files:**
- Create: `src/tradecraft/services/jue_wiki_repair_health.py`
- Create: `tests/test_jue_wiki_repair_health.py`
- Modify: `src/tradecraft/services/jue_wiki.py:89-110, 1495-1595`
- Modify: `src/tradecraft/api/ops_payloads.py:2140-2255, 2427-2520`
- Modify: `src/tradecraft/runtime/jue_wiki_runner.py:100-230`
- Modify: `src/tradecraft/config.py:535-565`
- Modify: `docs/spec/12_config_env.md`
- Test: `tests/test_jue_wiki.py:4500-4700`
- Test: `tests/test_ops_payloads.py:1800-1880, 2450-2610`

**Interfaces:**
- Consumes: repair queue counts and timestamps produced by `JueWikiService._repair_queue_status()`.
- Produces: `evaluate_repair_queue_health(queue, policy, now) -> dict[str, Any]` with `status`, `warning_signals`, `advisory_signals`, and progress metrics.

- [ ] **Step 1: Write failing pure health-policy tests**

```python
def test_progressing_open_queue_is_advisory_not_warning() -> None:
    health = evaluate_repair_queue_health(
        {
            "open_count": 346,
            "oldest_open_at": "2026-07-09T00:00:00+00:00",
            "last_resolved_at": "2026-07-10T00:55:00+00:00",
            "opened_in_window": 20,
            "resolved_in_window": 40,
        },
        policy=WikiRepairHealthPolicy(),
        now=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc),
    )
    assert health["warning_signals"] == []
    assert health["advisory_signals"] == ["jue_wiki_repair_queue_open"]


def test_stalled_overdue_queue_is_operational_warning() -> None:
    health = evaluate_repair_queue_health(
        {
            "open_count": 25,
            "oldest_open_at": "2026-07-07T00:00:00+00:00",
            "last_resolved_at": "2026-07-07T00:00:00+00:00",
            "opened_in_window": 30,
            "resolved_in_window": 0,
        },
        policy=WikiRepairHealthPolicy(),
        now=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc),
    )
    assert "jue_wiki_repair_queue_overdue" in health["warning_signals"]
    assert "jue_wiki_repair_queue_stalled" in health["warning_signals"]
```

- [ ] **Step 2: Run the tests and confirm module absence**

Run `pytest tests/test_jue_wiki_repair_health.py -q`.

Expected: collection ERROR.

- [ ] **Step 3: Implement the pure policy**

```python
@dataclass(frozen=True)
class WikiRepairHealthPolicy:
    overdue_sec: int = 86_400
    stall_sec: int = 21_600
    growth_window_sec: int = 86_400
    growth_warn_count: int = 25


def age_seconds(value: Any, now: datetime) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(int((now - parsed.astimezone(timezone.utc)).total_seconds()), 0)


def evaluate_repair_queue_health(
    queue: dict[str, Any],
    *,
    policy: WikiRepairHealthPolicy,
    now: datetime,
) -> dict[str, Any]:
    open_count = max(int(queue.get("open_count") or 0), 0)
    oldest_age = age_seconds(queue.get("oldest_open_at"), now)
    progress_age = age_seconds(queue.get("last_resolved_at"), now)
    opened = max(int(queue.get("opened_in_window") or 0), 0)
    resolved = max(int(queue.get("resolved_in_window") or 0), 0)
    warnings: list[str] = []
    if open_count and oldest_age is not None and oldest_age > policy.overdue_sec:
        warnings.append("jue_wiki_repair_queue_overdue")
    if open_count and (progress_age is None or progress_age > policy.stall_sec):
        warnings.append("jue_wiki_repair_queue_stalled")
    if open_count and opened - resolved >= policy.growth_warn_count:
        warnings.append("jue_wiki_repair_queue_growing")
    return {
        "status": "warning" if warnings else "progressing" if open_count else "idle",
        "warning_signals": warnings,
        "advisory_signals": ["jue_wiki_repair_queue_open"] if open_count else [],
        "oldest_open_age_sec": oldest_age,
        "progress_age_sec": progress_age,
        "net_growth_in_window": opened - resolved,
    }
```

- [ ] **Step 4: Add queue timestamps and recent-window counts**

In `_repair_queue_status()`, add aggregate queries for the oldest open `created_at`, latest resolved `finished_at`, and created/resolved counts since `now - growth_window_sec`. Return them under `repair_health_inputs` and attach the pure evaluation as `repair_health`.

- [ ] **Step 5: Add additive Wiki repair-health settings**

```python
jue_wiki_repair_overdue_sec: int = Field(
    default=86_400,
    alias="TRADECRAFT_JUE_WIKI_REPAIR_OVERDUE_SEC",
)
jue_wiki_repair_stall_sec: int = Field(
    default=21_600,
    alias="TRADECRAFT_JUE_WIKI_REPAIR_STALL_SEC",
)
jue_wiki_repair_growth_window_sec: int = Field(
    default=86_400,
    alias="TRADECRAFT_JUE_WIKI_REPAIR_GROWTH_WINDOW_SEC",
)
jue_wiki_repair_growth_warn_count: int = Field(
    default=25,
    alias="TRADECRAFT_JUE_WIKI_REPAIR_GROWTH_WARN_COUNT",
)
```

Pass these values into `JueWikiConfig`/`WikiRepairHealthPolicy` and document them. Do not modify `.env`.

- [ ] **Step 6: Move count-only Wiki signals to advisories**

Replace `_jue_wiki_readiness_warnings()` with:

```python
def _jue_wiki_readiness_signals(
    *,
    enabled: bool,
    runner: dict[str, Any],
    wiki_open_lint_count: int,
    wiki_stale_page_count: int,
    active_page_count: int,
    prompt_pressure: dict[str, Any],
    application: dict[str, Any],
    requested_symbol_coverage: dict[str, Any],
    repair_health: dict[str, Any],
    research_coverage: dict[str, Any],
) -> dict[str, list[str]]:
    warnings = list((repair_health or {}).get("warning_signals") or [])
    advisories = list((repair_health or {}).get("advisory_signals") or [])
    if _safe_int(coverage.get("prompt_omitted_count")) > 0:
        advisories.append("jue_wiki_requested_symbol_summaries_prompt_omitted")
    if _safe_int(coverage.get("degraded_summary_count")) > 0:
        advisories.append("jue_wiki_requested_symbol_summaries_degraded")
    return {
        "warnings": list(dict.fromkeys(warnings)),
        "advisories": list(dict.fromkeys(advisories)),
    }
```

Missing requested summaries remain warnings only when coverage is absent and no repair progress exists. Repair-pressure counts remain metrics/advisories while progress is healthy.

- [ ] **Step 7: Merge section advisories without changing operational status**

Update `merge_section_readiness_signals()` to merge `section_payload["advisories"]`, rebuild split actions, and leave `status=green` when only advisories exist.

- [ ] **Step 8: Run Wiki and signal tests**

Run:

```bash
pytest tests/test_jue_wiki_repair_health.py tests/test_jue_wiki.py tests/test_ops_payloads.py -q
```

Expected: PASS; progressing backlog produces no operational warning.

- [ ] **Step 9: Record a no-commit checkpoint**

Run `git diff --check` on the files in this task. Expected: exit 0. Do not stage or commit.

### Task 5: Deduplicate Open Wiki Repairs and Resolve Equivalent Work Atomically

**Files:**
- Modify: `src/tradecraft/services/jue_wiki.py:720-1140, 1880-2035, 3000-3060, 11070-11110`
- Test: `tests/test_jue_wiki.py`

**Interfaces:**
- Consumes: `finding_id`, `page_id`, `action_type`, `decision_scope`, symbols, and repair targets.
- Produces: `_repair_identity(finding_id, page_id, action_type, details) -> str`, `_record_or_refresh_repair_action(finding_id, page_id, action_type, status, details) -> dict[str, Any]`, and `resolve_duplicate_open_repair_actions() -> dict[str, int]`.

- [ ] **Step 1: Write failing deduplication tests**

```python
def test_record_repair_action_reuses_equivalent_open_work(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.record_repair_action(
        finding_id="financials:005930",
        page_id="kis.symbol.005930",
        action_type="refresh_financials",
        status="scheduled",
        details={"decision_scope": "kis", "symbols": ["005930"]},
    )
    second = service.record_repair_action(
        finding_id="financials:005930",
        page_id="kis.symbol.005930",
        action_type="refresh_financials",
        status="scheduled",
        details={"decision_scope": "kis", "symbols": ["005930"]},
    )
    assert second["action_id"] == first["action_id"]
    assert service.project_status_snapshot()["repair_queue"]["open_count"] == 1


def test_resolving_one_identity_resolves_all_equivalent_open_rows(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.initialize()
    details = {
        "decision_scope": "kis",
        "symbols": ["005930"],
        "repair_identity": "kis:financials:005930",
    }
    with service._connect() as conn:
        for index in range(2):
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, ?, ?, ?, 'scheduled', ?, ?, '', '')
                """,
                (
                    f"legacy-{index}",
                    "financials:005930",
                    "kis.symbol.005930",
                    "refresh_financials",
                    json.dumps(details),
                    f"2026-07-10T00:00:0{index}+00:00",
                ),
            )
    result = service.resolve_repair_identity(
        repair_identity="kis:financials:005930",
        resolved_by="financials_refreshed",
    )
    assert result["resolved_count"] == 2
    assert all(row["status"] == "resolved" for row in result["rows"])
```

- [ ] **Step 2: Run the focused tests and confirm duplicate rows**

Run `pytest tests/test_jue_wiki.py -k 'equivalent_open_work or resolves_all_equivalent' -q`.

Expected: FAIL because `record_repair_action()` hashes the current time.

- [ ] **Step 3: Implement a stable identity without deleting history**

```python
def _repair_identity(
    *,
    finding_id: str,
    page_id: str,
    action_type: str,
    details: dict[str, Any],
) -> str:
    scope = str(
        details.get("decision_scope")
        or details.get("scope")
        or details.get("source_scope")
        or "unknown"
    ).strip().lower()
    symbols = sorted(_normalize_symbol(item) for item in details.get("symbols") or [])
    raw = ":".join([scope, finding_id, page_id, action_type, ",".join(symbols)])
    return _hash_text(raw)[:32]
```

Store `repair_identity` inside `details_json`. Before inserting, query scheduled/unresolved rows for that identity and update the oldest row's latest evidence. Never replace or delete a resolved audit row.

- [ ] **Step 4: Resolve pre-existing duplicate open rows**

Group open rows by computed identity, keep the oldest as active, and update every later duplicate to `status='resolved'`, `finished_at=now`, with `resolved_by='duplicate_open_repair_action'` in `details_json`. Do not delete rows.

- [ ] **Step 5: Make successful target resolution identity-wide**

When `_resolve_repair_actions_for_clean_targets()` or manager evidence recovery succeeds, update every scheduled/unresolved row with the same identity in the same SQLite transaction.

- [ ] **Step 6: Run the full Wiki domain tests**

Run `pytest tests/test_jue_wiki.py tests/test_jue_wiki_application.py -q`.

Expected: PASS with duplicate audit rows preserved as resolved.

- [ ] **Step 7: Record a no-commit checkpoint**

Run `git diff --check -- src/tradecraft/services/jue_wiki.py tests/test_jue_wiki.py`. Expected: exit 0.

### Task 6: Supervise Naver Reports with a Killable Child and Stage Heartbeats

**Files:**
- Create: `src/tradecraft/runtime/naver_reports_worker.py`
- Modify: `src/tradecraft/runtime/naver_reports_runner.py:1-290`
- Modify: `src/tradecraft/services/intelligence.py:210-290`
- Modify: `src/tradecraft/runtime/watchdog_runner.py:213-345`
- Modify: `src/tradecraft/config.py:1660-1710`
- Modify: `docs/spec/12_config_env.md`
- Test: `tests/test_naver_reports_runner.py`
- Test: `tests/test_watchdog_runner.py`

**Interfaces:**
- Consumes: existing report stack and `naver_reports_cycle_timeout_sec`.
- Produces: worker result/progress snapshots; parent fields `stage`, `stage_started_at`, `heartbeat_at`, `deadline_at`, and `worker_pid`.

- [ ] **Step 1: Write failing child-timeout and heartbeat tests**

```python
def test_report_supervisor_kills_worker_after_deadline(tmp_path: Path) -> None:
    class FakeProcess:
        pid = 321

        def __init__(self) -> None:
            self.terminate_calls = 0
            self.kill_calls = 0

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.kill_calls += 1

    ticks = iter([0.0, 1.0, 3.0])
    process = FakeProcess()
    result = supervise_report_worker(
        process=process,
        result_store=RuntimeStateStore(tmp_path / "result.json"),
        progress_store=RuntimeStateStore(tmp_path / "progress.json"),
        parent_state=RuntimeStateStore(tmp_path / "parent.json"),
        timeout_sec=2,
        heartbeat_interval_sec=1,
        monotonic=lambda: next(ticks),
        sleep=lambda _: None,
    )
    assert result["status"] == "timeout"
    assert process.terminate_calls == 1
    assert process.kill_calls == 1


def test_watchdog_marks_stale_naver_report_heartbeat_for_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "naver_reports.json"
    state_path.write_text(json.dumps({
        "status": "collecting",
        "heartbeat_at": "2026-07-10T00:00:00+00:00",
        "deadline_at": "2026-07-10T00:30:00+00:00",
    }))
    settings = SimpleNamespace(
        naver_reports_state_path=str(state_path),
        naver_reports_cycle_timeout_sec=1800,
    )
    row = watchdog_runner._annotate_runtime_state_health(
        "naver_reports",
        {"alive": True, "direct_alive": True},
        settings,
        now=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc),
    )
    assert row["stale_runtime_reason"] == "naver_reports_heartbeat_overdue"
```

- [ ] **Step 2: Run the focused tests and confirm no child boundary exists**

Run `pytest tests/test_naver_reports_runner.py tests/test_watchdog_runner.py -k 'report_supervisor or naver_report_heartbeat' -q`.

Expected: FAIL.

- [ ] **Step 3: Add stage progress to the collection cycle**

```python
ProgressFn = Callable[[str, dict[str, Any]], None]


def _progress(callback: ProgressFn | None, stage: str, **detail: Any) -> None:
    if callback is not None:
        callback(stage, detail)
```

Emit stages before and after `crawl_once`, symbol refresh, metadata repair, RAG document sync, and RAG metadata sync. Existing callers pass `None`, so behavior remains compatible.

- [ ] **Step 4: Implement the one-cycle worker**

`naver_reports_worker.py` builds the existing report intelligence stack, writes progress atomically on each callback, runs one cycle, and writes either a result or an error snapshot. It performs no scheduling and exits after one cycle. Its CLI accepts exact `--result-path` and `--progress-path` arguments supplied by the parent.

- [ ] **Step 5: Implement parent supervision**

```python
while process.poll() is None:
    progress = progress_store.read_snapshot() or {}
    parent_state.write_snapshot({
        "status": "collecting",
        "stage": str(progress.get("stage") or "starting"),
        "stage_started_at": str(progress.get("stage_started_at") or started_at),
        "heartbeat_at": _utc_now_iso(),
        "deadline_at": deadline_at,
        "worker_pid": process.pid,
    })
    if monotonic() >= deadline:
        process.terminate()
        if not wait_for_exit(process, grace_sec=5.0):
            process.kill()
        return {"status": "timeout", "timeout_sec": timeout_sec}
    sleep(heartbeat_interval_sec)
```

Implement `wait_for_exit(process, grace_sec, monotonic, sleep) -> bool` beside the supervisor by polling until the grace deadline. Use a 5-second default heartbeat and existing cycle timeout. A timed-out child is killed before the next cycle can start.

- [ ] **Step 6: Fix watchdog state routing**

Use key `naver_reports` and `settings.naver_reports_state_path`; do not map report supervision through the legacy `research` key. A missed heartbeat or deadline sets `stale_process=True` and a precise reason.

- [ ] **Step 7: Add additive supervisor timing settings**

```python
naver_reports_heartbeat_interval_sec: float = Field(
    default=5.0,
    alias="TRADECRAFT_NAVER_REPORTS_HEARTBEAT_INTERVAL_SEC",
)
naver_reports_worker_terminate_grace_sec: float = Field(
    default=5.0,
    alias="TRADECRAFT_NAVER_REPORTS_WORKER_TERMINATE_GRACE_SEC",
)
```

Document the aliases and pass them to the parent supervisor. Do not change the existing collection interval or cycle-timeout value.

- [ ] **Step 8: Run report/watchdog domain tests**

Run:

```bash
pytest tests/test_naver_reports_runner.py tests/test_watchdog_runner.py tests/test_api_smoke.py -k 'naver or report or watchdog' -q
```

Expected: PASS.

- [ ] **Step 9: Record a no-commit checkpoint**

Run `git diff --check` on the Task 6 files. Expected: exit 0.

### Task 7: Enforce a Typed Market Judge Prompt Budget Before LLM Invocation

**Files:**
- Create: `src/tradecraft/services/market_judge_prompt.py`
- Create: `tests/test_market_judge_prompt.py`
- Modify: `src/tradecraft/services/market_judgment.py:134-155, 3780-3880, 4171-4440`
- Modify: `src/tradecraft/runtime/market_judge_runner.py:360-420`
- Modify: `src/tradecraft/config.py:1870-1905`
- Modify: `docs/spec/12_config_env.md`
- Test: `tests/test_market_judgment.py`

**Interfaces:**
- Consumes: the existing Market Judge prompt dictionary.
- Produces: `finalize_market_judge_prompt(prompt, *, target_chars, warn_chars, max_chars) -> MarketJudgePromptBundle` and `prompt_budget_contract_violation` on overflow.

- [ ] **Step 1: Write failing core-type and overflow tests**

```python
@pytest.mark.parametrize("repeat", [1, 20, 200])
def test_market_judge_prompt_budget_preserves_core_types(repeat: int) -> None:
    prompt = {
        "account": {"orderable_cash_krw": 1_000_000},
        "symbols": [
            {
                "symbol": f"{index:06d}",
                "quote": {"price": 70_000},
                "strategy": {"score": 0.8},
                "rag": [{"content": "근거" * repeat * 200}],
            }
            for index in range(60)
        ],
        "strategy_summary": {"status": "ok", "sources": ["naver_reports"]},
        "market_pulse": {"status": "ok"},
        "investment_memory": {"status": "ok", "notes": ["기억" * repeat * 100]},
    }
    bundle = finalize_market_judge_prompt(
        prompt,
        target_chars=120_000,
        warn_chars=150_000,
        max_chars=190_000,
    )
    runtime = bundle.runtime_prompt
    assert isinstance(runtime["symbols"], list)
    assert isinstance(runtime["account"], dict)
    assert isinstance(runtime["strategy_summary"], dict)
    assert len(json.dumps(runtime, ensure_ascii=False)) <= 190_000


def test_market_judge_budget_violation_skips_llm(tmp_path: Path) -> None:
    class CountingLLM:
        ready = True
        resolved_model = "test"

        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
            self.calls += 1
            return {"ok": True, "content": "{\"judgments\": []}"}

    llm = CountingLLM()
    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=1,
            llm_max_symbols=1,
            prompt_max_chars=10_000,
        ),
        kis=_FakeKIS(),
        codex_runtime=llm,
        strategy_engine=_FakeStrategy(),
        calendar=_OpenCalendar(),
    )
    engine._build_prompt = lambda **_: {
        "account": {"irreducible": "x" * 20_000},
        "symbols": [{"symbol": "005930"}],
        "strategy_summary": {},
    }
    result = asyncio.run(engine.run_once(use_llm=True))
    assert result["status"] == "error"
    assert result["error_message"].startswith("prompt_budget_contract_violation")
    assert llm.calls == 0
```

- [ ] **Step 2: Run focused tests and confirm Market Judge calls LLM without the contract**

Run `pytest tests/test_market_judge_prompt.py tests/test_market_judgment.py -k 'prompt_budget or budget_violation' -q`.

Expected: FAIL.

- [ ] **Step 3: Define the Market Judge-specific typed bundle**

```python
@dataclass(frozen=True)
class MarketJudgePromptCoreV1:
    account: dict[str, Any]
    symbols: list[dict[str, Any]]
    strategy_summary: dict[str, Any]
    version: str = "market_judge_prompt_core_v1"


@dataclass(frozen=True)
class MarketJudgePromptBundle:
    runtime_prompt: dict[str, Any]
    audit_prompt: dict[str, Any]
    core: MarketJudgePromptCoreV1
    compaction_meta: dict[str, Any]
    version: str = "market_judge_prompt_bundle_v1"
```

Reject a non-dict `account`/`strategy_summary`, non-list `symbols`, or non-dict symbol item with `ManagerPromptContractViolation`. Do not reuse `ManagerPromptCoreV2`, whose KIS/Binance keys are `decision_inputs`, `candidates`, and `blocks`.

- [ ] **Step 4: Implement bounded optional-evidence compaction**

Rank and trim `symbols[*].rag`, valuation details, prior judgment detail, memory analysis rows, and source diagnostics. Preserve the types of `symbols`, `account`, `strategy_summary`, `market_pulse`, and `investment_memory`. Record original/included counts under `compaction_meta`; never replace a list/dict with `{item_count, items}` in the runtime prompt.

Implement `compact_market_judge_audit_prompt(prompt) -> dict[str, Any]` in the same module. It may use `{item_count, items}` for stored `symbols` but is never passed to `_run_llm()`.

- [ ] **Step 5: Add additive Market Judge budget settings**

```python
market_judge_prompt_target_chars: int = Field(
    default=120_000,
    alias="TRADECRAFT_MARKET_JUDGE_PROMPT_TARGET_CHARS",
)
market_judge_prompt_warn_chars: int = Field(
    default=150_000,
    alias="TRADECRAFT_MARKET_JUDGE_PROMPT_WARN_CHARS",
)
market_judge_prompt_max_chars: int = Field(
    default=190_000,
    alias="TRADECRAFT_MARKET_JUDGE_PROMPT_MAX_CHARS",
)
```

Add matching dataclass fields and pass the settings through the runner:

```python
@dataclass(slots=True)
class MarketJudgmentConfig:
    # existing fields remain unchanged
    prompt_target_chars: int = 120_000
    prompt_warn_chars: int = 150_000
    prompt_max_chars: int = 190_000
```

Do not change the current model, schedule, or trading settings.

- [ ] **Step 6: Stop before LLM on contract error**

```python
audit_prompt = compact_market_judge_audit_prompt(raw_prompt)
try:
    bundle = finalize_market_judge_prompt(
        raw_prompt,
        target_chars=self.config.prompt_target_chars,
        warn_chars=self.config.prompt_warn_chars,
        max_chars=self.config.prompt_max_chars,
    )
    prompt = bundle.runtime_prompt
    budget_error = ""
except ManagerPromptContractViolation as exc:
    prompt = audit_prompt
    budget_error = str(exc)

if budget_error:
    status = "error"
    mode = "error"
    error_message = budget_error
else:
    llm_result = await self._run_llm(prompt)
```

Persist the error run for audit, but do not call LLM or any downstream action.

- [ ] **Step 7: Run Market Judge tests**

Run `pytest tests/test_market_judge_prompt.py tests/test_market_judgment.py tests/test_config.py -q`.

Expected: PASS and maximum runtime prompt <=190,000 chars.

- [ ] **Step 8: Record a no-commit checkpoint**

Run `git diff --check` on the Task 7 files. Expected: exit 0.

### Task 8: Reduce KIS Prompt Pressure and Make Large-Prompt Recovery Evidence-Based

**Files:**
- Modify: `src/tradecraft/services/kis_manager_prompt.py:11143-12250`
- Modify: `src/tradecraft/api/llm_payloads.py:20-220`
- Test: `tests/test_kis_manager_prompt.py:9840-12700`
- Test: `tests/test_llm_payloads.py`

**Interfaces:**
- Consumes: existing KIS `finalize_prompt_budget()` and LLM usage enrichment rows.
- Produces: KIS runtime prompt below warn when reducible, plus `ok_under_warn_after_large_count` and `latest_large_prompt_at` telemetry fields.

- [ ] **Step 1: Write failing max-size KIS type tests**

```python
def test_kis_warn_budget_preserves_core_types_under_large_optional_context() -> None:
    prompt = {
        "decision_inputs": ["account", "quotes", "risk"],
        "candidates": [{"symbol": f"{index:06d}", "evidence": "x" * 2_000} for index in range(80)],
        "blocks": [{"block_id": f"block-{index}", "notes": "y" * 1_000} for index in range(40)],
        "recent_events": [{"detail": "z" * 1_000} for _ in range(300)],
        "research_spine": {"items": [{"content": "r" * 2_000} for _ in range(100)]},
    }
    finalize_prompt_budget(
        prompt,
        target_chars=120_000,
        warn_chars=150_000,
        max_chars=190_000,
    )
    assert isinstance(prompt["decision_inputs"], dict)
    assert isinstance(prompt["candidates"], list)
    assert isinstance(prompt["blocks"], list)
    assert prompt["prompt_budget"]["over_warn"] is False
    assert prompt_budget_error(prompt) == ""
```

- [ ] **Step 2: Write failing healthy-window tests**

```python
def test_large_prompt_warning_requires_three_under_warn_successes_to_clear() -> None:
    def payload(successes: int) -> dict[str, Any]:
        return {
            "today": {
                "by_component": [{
                    "component": "market_judge",
                    "call_count": 5,
                    "error_count": 0,
                    "max_input_chars": 355_000,
                    "avg_input_chars": 220_000,
                    "latest_input_chars": 120_000,
                    "latest_status": "ok",
                    "latest_large_prompt_at": "2026-07-10T00:00:00+00:00",
                    "ok_under_warn_after_large_count": successes,
                }]
            }
        }

    result = build_llm_usage_semantic_check(payload(2))
    assert "llm_prompt_payload_large" in result["warnings"]
    recovered = build_llm_usage_semantic_check(
        payload(3)
    )
    assert "llm_prompt_payload_large" not in recovered["warnings"]
```

- [ ] **Step 3: Run tests and confirm the current one-success recovery is too weak**

Run `pytest tests/test_kis_manager_prompt.py -k 'warn_budget_preserves_core_types' tests/test_llm_payloads.py -k 'large_prompt_warning_requires' -q`.

Expected: FAIL.

- [ ] **Step 4: Tighten KIS optional-section compaction**

Before emergency core compaction, cap high-cardinality optional evidence in `decision_packet`, `pre_adoption_symbol_analysis`, `jue_wiki`, `research_spine`, `opportunity_research_brief`, `recent_events`, and policy impact rows. Preserve required core list/dict types and add original/included counts to `prompt_compaction`.

- [ ] **Step 5: Enrich and evaluate large-prompt recovery**

Query the latest large prompt timestamp per component and count successful calls below 150,000 chars after it. A component is recovered when either its runner started after the last large call or at least three under-warn successes occurred. Keep the existing stale-after-restart evidence path.

- [ ] **Step 6: Run prompt and telemetry tests**

Run:

```bash
pytest tests/test_kis_manager_prompt.py tests/test_manager_prompt_budget.py tests/test_llm_payloads.py -q
```

Expected: PASS; no LLM call occurs when core data cannot fit.

- [ ] **Step 7: Record a no-commit checkpoint**

Run `git diff --check -- src/tradecraft/services/kis_manager_prompt.py src/tradecraft/api/llm_payloads.py tests/test_kis_manager_prompt.py tests/test_llm_payloads.py`.

### Task 9: Add Verified Sequential Runner Recovery

**Files:**
- Create: `src/tradecraft/runtime/runner_recovery.py`
- Create: `tests/test_runner_recovery.py`
- Modify: `src/tradecraft/runtime/process_status.py:283-430`
- Modify: `src/tradecraft/runtime/watchdog_runner.py:479-585`
- Modify: `src/tradecraft/api/ops.py:830-875`
- Test: `tests/test_process_status.py`
- Test: `tests/test_watchdog_runner.py`
- Test: `tests/test_ops_api_router.py`

**Interfaces:**
- Consumes: `restart_runner_processes([key])` and `runner_process_status(key)`.
- Produces: `recover_runners_rolling(keys, *, restart_one, status_provider, verify_timeout_sec, poll_interval_sec, sleep) -> dict[str, Any]`.

- [ ] **Step 1: Write failing sequential-verification tests**

```python
def test_rolling_recovery_verifies_each_runner_before_next() -> None:
    events: list[str] = []
    rows = {
        "jue_wiki": iter([
            {"alive": True, "pid": 10, "started_at_epoch": 1, "pid_file_status": "ok", "stale_process": True},
            {"alive": True, "pid": 11, "started_at_epoch": 2, "pid_file_status": "ok", "stale_process": False},
        ]),
        "control": iter([
            {"alive": True, "pid": 20, "started_at_epoch": 1, "pid_file_status": "ok", "stale_process": True},
            {"alive": True, "pid": 21, "started_at_epoch": 2, "pid_file_status": "ok", "stale_process": False},
        ]),
    }
    result = recover_runners_rolling(
        ["jue_wiki", "control"],
        restart_one=lambda key: events.append(f"restart:{key}"),
        status_provider=lambda key: next(rows[key]),
        sleep=lambda _: None,
    )
    assert events == ["restart:jue_wiki", "restart:control"]
    assert result["verified_keys"] == ["jue_wiki", "control"]


def test_rolling_recovery_stops_after_failed_verification() -> None:
    events: list[str] = []
    result = recover_runners_rolling(
        ["kis_block_trader", "control"],
        restart_one=lambda key: events.append(f"restart:{key}"),
        status_provider=lambda key: {"alive": False, "status": "stopped"},
        verify_timeout_sec=0,
        sleep=lambda _: None,
    )
    assert result["status"] == "verification_failed"
    assert events == ["restart:kis_block_trader"]
```

- [ ] **Step 2: Run the tests and confirm current restart is unverified**

Run `pytest tests/test_runner_recovery.py tests/test_process_status.py -k 'rolling_recovery' -q`.

Expected: FAIL.

- [ ] **Step 3: Implement the verification contract**

A runner is verified only when it is alive, has a new PID or start epoch, `stale_process` is false, `pid_file_status` is `ok`, and its runtime heartbeat is not stale. Record before/after status and elapsed time for every key.

- [ ] **Step 4: Enforce safe order**

Normalize the recovery order so data producers and decision runners restart before `control`, and `control` is always last. KIS and Binance must never be restarted concurrently. Unknown keys remain rejected by the existing allowlist.

- [ ] **Step 5: Schedule recovery from an external supervisor**

The API returns before replacing `control`. Launch the rolling coordinator in a detached Python process, persist progress to `.runtime/runner_recovery.json`, and preserve the active-trading confirmation checks already present in `/api/ops/restart`.

- [ ] **Step 6: Update watchdog to schedule one candidate at a time**

When multiple candidates exist, schedule the first, verify on the next watchdog check, then continue. Flap/cooldown policies remain unchanged.

- [ ] **Step 7: Run process, API, and watchdog tests**

Run:

```bash
pytest tests/test_runner_recovery.py tests/test_process_status.py tests/test_watchdog_runner.py tests/test_ops_api_router.py -q
```

Expected: PASS.

- [ ] **Step 8: Record a no-commit checkpoint**

Run `git diff --check` on the Task 9 files. Expected: exit 0.

### Task 10: Apply Recovery and Prove the Actual Runtime Is Green

**Files:**
- Modify: `tests/test_readiness_performance.py`
- Modify: `tests/test_runtime_test_isolation.py`
- Update: `docs/superpowers/plans/2026-07-10-hermes-continuous-implementation-log.md`

**Interfaces:**
- Consumes: the completed code from Tasks 1-9 and authenticated local readiness API.
- Produces: an acceptance record with warnings/blockers, latency, process freshness, database checksums, and order-count invariants.

- [ ] **Step 1: Capture pre-verification safety evidence**

Run a read-only Python script that records SHA-256, size, and `st_mtime_ns` for every `.runtime/*.db` and state file used by the tests. Separately record `SELECT COUNT(*) FROM block_orders` for KIS and Binance databases. Save the report under `.runtime/verification/hermes-readiness-before.json`; do not print credentials.

- [ ] **Step 2: Run focused test groups**

Run:

```bash
pytest tests/test_ops_readiness_snapshot.py tests/test_ops_payloads.py tests/test_ops_api_router.py tests/test_static_ui.py -q
pytest tests/test_jue_wiki_repair_health.py tests/test_jue_wiki.py tests/test_jue_wiki_application.py -q
pytest tests/test_naver_reports_runner.py tests/test_watchdog_runner.py tests/test_runner_recovery.py tests/test_process_status.py -q
pytest tests/test_market_judge_prompt.py tests/test_market_judgment.py tests/test_kis_manager_prompt.py tests/test_llm_payloads.py -q
```

Expected: all PASS.

- [ ] **Step 3: Run fast and full project verification**

Run:

```bash
python scripts/verify.py fast
python scripts/verify.py domain --area binance
python scripts/verify.py domain --area kis
python scripts/verify.py full
ruff check src tests
```

Expected: all commands exit 0. If a pre-existing unrelated failure appears, record its exact node ID and continue only after proving it is unrelated or fixing it within scope.

- [ ] **Step 4: Apply rolling recovery to stale runners**

Recover in this order:

```text
naver_reports -> investment_memory -> live_evaluator -> jue_wiki -> market_judge -> kis_block_trader -> control
```

After each key, require a new PID/start epoch, fresh source, healthy heartbeat, and non-error domain state. Stop immediately on failed verification. Do not invoke manager/executor endpoints.

- [ ] **Step 5: Verify Naver Reports freshness**

Wait for the supervised collection worker to complete or return a verified no-new-content result. `reports_db_stale` may clear only when the stored report source timestamp/provenance advances; a process restart is insufficient.

- [ ] **Step 6: Verify prompt warning recovery**

Confirm Market Judge and KIS runtime code is source-fresh and the old large prompt telemetry is classified as pre-restart. Do not force a live manager or judgment run. Future runs remain bounded by the new contracts.

- [ ] **Step 7: Measure authenticated readiness latency**

Run 20 warm compact requests and at least five cold full snapshot reads. Record p50/p95/max. Required results:

```text
compact warm p95 <= 0.500s
full cold p95 <= 2.000s
SQLite writes during requests = 0
```

- [ ] **Step 8: Verify final signal and UI state**

Require:

```json
{
  "status": "green",
  "blockers": [],
  "warnings": []
}
```

The advisory list may remain non-empty. In the authenticated local UI, verify the global banner is hidden and the KIS/Binance workspace still shows strategy advisory/restriction details.

- [ ] **Step 9: Compare post-verification safety evidence**

Recompute order counts and runtime checksums. Order counts must be unchanged. Test-isolated live database/state files must have identical checksum and mtime. Expected runtime changes are limited to approved runner states, logs, readiness snapshots, Wiki repair status updates, and report collection output; list each changed runtime file explicitly.

- [ ] **Step 10: Write the acceptance record**

Append the following concrete fields to the continuous implementation log:

```text
completed_at
baseline_signals
final_signals
changed_files
verification_commands
latency_p50_p95_max
sqlite_write_count
runner_before_after
runtime_checksum_comparison
kis_order_count_before_after
binance_order_count_before_after
remaining_strategy_advisories
remaining_risks
```

- [ ] **Step 11: Run final diff checks without committing**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors. Preserve unrelated user changes and do not commit.

## Completion Gate

Do not declare completion unless all of the following are proven from the current runtime:

1. Authenticated compact readiness returns `green`, no blockers, and no warnings.
2. The global operational banner is hidden.
3. KIS/Binance advisories and server-side safety restrictions remain present.
4. Compact warm p95 <=500 ms and full cold p95 <=2 s.
5. Readiness requests write no SQLite or state files.
6. Every default enabled runner is alive, source-fresh, and heartbeat-fresh.
7. Naver Reports has a completed/provenanced current cycle.
8. No order count changed during implementation verification.
9. Focused, domain, fast, full, and ruff checks pass.
10. No live setting change, data deletion, or commit occurred.
