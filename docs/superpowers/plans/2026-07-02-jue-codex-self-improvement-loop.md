# Jue Codex Self-Improvement Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous Codex-native repair loop that converts Jue's validation warnings/failures into tested, deployed, and Wiki-recorded improvements without requiring human approval for operational code changes.

**Architecture:** Introduce a `JueCodexLab` service that reads trading validation work queues, turns them into executable repair tasks, asks Codex native to design and patch fixes in an isolated workspace, verifies them with deterministic tests and runtime smoke checks, applies passing patches automatically, restarts affected runners, and records the result back into Jue Wiki and memory. Human approval is not a gate; machine gates, rollback snapshots, and telemetry are mandatory.

**Tech Stack:** Python 3.10, SQLite, existing `CodexNativeRuntime`, FastAPI route group pattern, pytest, tmux runner restart utilities, Jue Wiki, Investment Memory, Telegram CLI.

---

## Brainstorming Summary

The current 19-validation system diagnoses problems but does not reliably close the loop from warning/failure to repair. The right shift is not to remove the validation dashboard, but to turn every persistent yellow/red item into an autonomous Codex repair objective.

Three approaches were considered:

1. **Prompt-only repair loop**
   - Feed validation warnings into KIS/Binance manager prompts and ask Jue to behave better.
   - Low risk, but already close to the current state and unlikely to turn warnings green.

2. **Runner-only repair loop**
   - Add scripted jobs for known repair hooks such as cost sync, pattern lab rebuild, and risk budget refresh.
   - Useful, but limited to known repair types and weak for code defects or missing instrumentation.

3. **Codex-native autonomous repair loop**
   - Treat validation failures as code/data/research defects, let Codex native inspect the repo and runtime DBs, produce patches, run tests, deploy passing changes, and write the learning back into Wiki.
   - Recommended. This makes Jue self-improving while keeping safety in deterministic verification, not human approval.

## Non-Negotiable Design Rules

- Human approval is not required for operational code changes.
- No silent fallback. Failed repair attempts must remain failed and visible.
- Live order safety gates remain absolute: kill switch, cash/quantity checks, duplicate-order prevention, exchange/KIS API errors, stale quote checks.
- Codex may patch operational code automatically only through a verified patch pipeline.
- Every autonomous patch must have a task record, diff summary, verification result, deploy event, rollback pointer, and Jue Wiki memory.
- During open KRX regular market hours, KIS live execution-path patches may be staged but not hot-deployed unless the patch only affects observability, Wiki, tests, or non-ordering research code.
- Binance is 24-hour, so deployment uses canary windows and immediate rollback if post-deploy smoke checks fail.
- The loop's objective is to move validation items toward green, not to hide, demote, or hard-code around failing metrics.

## File Structure

Create:
- `src/tradecraft/services/jue_codex_lab.py`
  Orchestrates validation ingestion, task selection, Codex runs, verification, deployment, and status payloads.
- `src/tradecraft/services/jue_codex_lab_models.py`
  Dataclasses and typed helpers for tasks, runs, patches, verification, deployment, and green-path progress.
- `src/tradecraft/services/jue_codex_lab_store.py`
  SQLite schema and repository for `.runtime/jue_codex_lab.db`.
- `src/tradecraft/services/jue_codex_repair_catalog.py`
  Maps validation disciplines and automation hooks to repair strategies, tests, allowed touch areas, and deployment rules.
- `src/tradecraft/services/jue_codex_patch_workspace.py`
  Creates isolated patch workspaces, snapshots touched files, applies generated patches, and produces rollback bundles.
- `src/tradecraft/services/jue_codex_verifier.py`
  Runs pytest, py_compile, diff checks, runtime smoke checks, and focused DB assertions.
- `src/tradecraft/runtime/jue_codex_lab_runner.py`
  Periodic autonomous runner that processes due repair tasks.
- `src/tradecraft/api/jue_codex_lab_router.py`
  Admin-protected API endpoints for status, run-once, task list, run details, and deployment history.
- `tests/test_jue_codex_lab_store.py`
- `tests/test_jue_codex_repair_catalog.py`
- `tests/test_jue_codex_lab.py`
- `tests/test_jue_codex_verifier.py`
- `tests/test_jue_codex_lab_runner.py`
- `tests/test_jue_codex_lab_api.py`

Modify:
- `src/tradecraft/config.py`
  Add settings for enabling the loop, cadence, autonomy mode, max patch size, allowed paths, blocked paths, and deployment windows.
- `src/tradecraft/main.py`
  Register API router and include Codex Lab status in `/api/ops/readiness`.
- `pyproject.toml`
  Add `tradecraft-jue-codex-lab` entrypoint.
- `src/tradecraft/runtime/process_status.py`
  Add runner restart spec/status for `jue_codex_lab`.
- `src/tradecraft/services/jue_wiki.py`
  Add Wiki pages for validation green path and Codex repair progress.
- `src/tradecraft/services/investment_memory.py`
  Record repair outcomes as growth memory.
- `src/tradecraft/services/telegram_cli.py`
  Add compact status commands and error notifications.
- `tests/test_api_smoke.py`
- `tests/test_process_status.py`
- `tests/test_jue_wiki.py`
- `tests/test_investment_memory.py`
- `tests/test_telegram_cli.py`

## Database Schema

Create `.runtime/jue_codex_lab.db` with:

```sql
CREATE TABLE repair_tasks (
    task_id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    discipline_id TEXT NOT NULL,
    source_validation_run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    owner TEXT NOT NULL,
    automation_hook TEXT NOT NULL,
    failure_status TEXT NOT NULL,
    failure_evidence TEXT NOT NULL DEFAULT '',
    green_condition_json TEXT NOT NULL DEFAULT '{}',
    allowed_paths_json TEXT NOT NULL DEFAULT '[]',
    blocked_paths_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    due_at TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_run_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE repair_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    codex_thread_key TEXT NOT NULL DEFAULT '',
    codex_thread_id TEXT NOT NULL DEFAULT '',
    prompt_json TEXT NOT NULL DEFAULT '{}',
    response_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE patch_attempts (
    patch_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    workspace_path TEXT NOT NULL DEFAULT '',
    diff_summary TEXT NOT NULL DEFAULT '',
    touched_paths_json TEXT NOT NULL DEFAULT '[]',
    patch_text TEXT NOT NULL DEFAULT '',
    rollback_snapshot_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE verification_results (
    verification_id TEXT PRIMARY KEY,
    patch_id TEXT NOT NULL,
    status TEXT NOT NULL,
    command TEXT NOT NULL,
    expected TEXT NOT NULL DEFAULT '',
    output_excerpt TEXT NOT NULL DEFAULT '',
    elapsed_sec REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE deployment_events (
    event_id TEXT PRIMARY KEY,
    patch_id TEXT NOT NULL,
    status TEXT NOT NULL,
    deployed_at TEXT NOT NULL,
    restarted_runners_json TEXT NOT NULL DEFAULT '[]',
    post_deploy_checks_json TEXT NOT NULL DEFAULT '{}',
    rollback_event_id TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE green_path_progress (
    progress_id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    discipline_id TEXT NOT NULL,
    before_status TEXT NOT NULL,
    after_status TEXT NOT NULL,
    before_score REAL NOT NULL DEFAULT 0,
    after_score REAL NOT NULL DEFAULT 0,
    validation_run_before TEXT NOT NULL DEFAULT '',
    validation_run_after TEXT NOT NULL DEFAULT '',
    repair_task_id TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL
);
```

---

### Task 1: Add Settings and Entry Point

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `pyproject.toml`
- Test: `tests/test_config.py`
- Test: `tests/test_entrypoints.py`

- [ ] **Step 1: Write failing settings test**

Add to `tests/test_config.py`:

```python
def test_jue_codex_lab_settings_defaults(monkeypatch) -> None:
    from tradecraft.config import AppSettings

    for key in (
        "TRADECRAFT_JUE_CODEX_LAB_ENABLED",
        "TRADECRAFT_JUE_CODEX_LAB_INTERVAL_SEC",
        "TRADECRAFT_JUE_CODEX_LAB_AUTONOMY_MODE",
        "TRADECRAFT_JUE_CODEX_LAB_MAX_PATCH_BYTES",
        "TRADECRAFT_JUE_CODEX_LAB_ALLOWED_PATHS",
        "TRADECRAFT_JUE_CODEX_LAB_BLOCKED_PATHS",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = AppSettings()

    assert settings.jue_codex_lab_enabled is True
    assert settings.jue_codex_lab_interval_sec == 1800
    assert settings.jue_codex_lab_autonomy_mode == "auto_apply_verified"
    assert settings.jue_codex_lab_max_patch_bytes == 120_000
    assert "src/tradecraft" in settings.jue_codex_lab_allowed_paths
    assert ".env" in settings.jue_codex_lab_blocked_paths
```

- [ ] **Step 2: Run failing settings test**

Run:

```bash
pytest tests/test_config.py::test_jue_codex_lab_settings_defaults -q
```

Expected: FAIL with missing `AppSettings` attributes.

- [ ] **Step 3: Add settings**

Add to `AppSettings` in `src/tradecraft/config.py`:

```python
jue_codex_lab_enabled: bool = Field(
    default=True,
    alias="TRADECRAFT_JUE_CODEX_LAB_ENABLED",
)
jue_codex_lab_interval_sec: int = Field(
    default=1800,
    alias="TRADECRAFT_JUE_CODEX_LAB_INTERVAL_SEC",
)
jue_codex_lab_autonomy_mode: str = Field(
    default="auto_apply_verified",
    alias="TRADECRAFT_JUE_CODEX_LAB_AUTONOMY_MODE",
)
jue_codex_lab_db_path: str = Field(
    default=".runtime/jue_codex_lab.db",
    alias="TRADECRAFT_JUE_CODEX_LAB_DB_PATH",
)
jue_codex_lab_max_patch_bytes: int = Field(
    default=120_000,
    alias="TRADECRAFT_JUE_CODEX_LAB_MAX_PATCH_BYTES",
)
jue_codex_lab_allowed_paths: str = Field(
    default=(
        "src/tradecraft,tests,docs/superpowers,pyproject.toml,"
        "README.md"
    ),
    alias="TRADECRAFT_JUE_CODEX_LAB_ALLOWED_PATHS",
)
jue_codex_lab_blocked_paths: str = Field(
    default=(
        ".env,.runtime,src/tradecraft/services/kis.py,"
        "src/tradecraft/services/binance.py"
    ),
    alias="TRADECRAFT_JUE_CODEX_LAB_BLOCKED_PATHS",
)
jue_codex_lab_max_tasks_per_cycle: int = Field(
    default=2,
    alias="TRADECRAFT_JUE_CODEX_LAB_MAX_TASKS_PER_CYCLE",
)
jue_codex_lab_market_hours_hot_deploy: bool = Field(
    default=False,
    alias="TRADECRAFT_JUE_CODEX_LAB_MARKET_HOURS_HOT_DEPLOY",
)
```

- [ ] **Step 4: Add entrypoint test**

Add to `tests/test_entrypoints.py`:

```python
def test_jue_codex_lab_entrypoint_is_registered() -> None:
    import tomllib
    from pathlib import Path

    data = tomllib.loads(Path("pyproject.toml").read_text())
    scripts = data["project"]["scripts"]

    assert scripts["tradecraft-jue-codex-lab"] == (
        "tradecraft.runtime.jue_codex_lab_runner:run"
    )
```

- [ ] **Step 5: Run failing entrypoint test**

Run:

```bash
pytest tests/test_entrypoints.py::test_jue_codex_lab_entrypoint_is_registered -q
```

Expected: FAIL because script is missing.

- [ ] **Step 6: Add entrypoint**

Add to `[project.scripts]` in `pyproject.toml`:

```toml
tradecraft-jue-codex-lab = "tradecraft.runtime.jue_codex_lab_runner:run"
```

- [ ] **Step 7: Verify Task 1**

Run:

```bash
pytest tests/test_config.py::test_jue_codex_lab_settings_defaults tests/test_entrypoints.py::test_jue_codex_lab_entrypoint_is_registered -q
```

Expected: both PASS.

---

### Task 2: Add Codex Lab Store

**Files:**
- Create: `src/tradecraft/services/jue_codex_lab_models.py`
- Create: `src/tradecraft/services/jue_codex_lab_store.py`
- Test: `tests/test_jue_codex_lab_store.py`

- [ ] **Step 1: Write failing repository schema test**

Create `tests/test_jue_codex_lab_store.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from tradecraft.services.jue_codex_lab_store import JueCodexLabStore


def test_store_initializes_repair_loop_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "jue_codex_lab.db"

    store = JueCodexLabStore(db_path)
    store.initialize()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "repair_tasks",
        "repair_runs",
        "patch_attempts",
        "verification_results",
        "deployment_events",
        "green_path_progress",
    } <= tables
```

- [ ] **Step 2: Run failing schema test**

Run:

```bash
pytest tests/test_jue_codex_lab_store.py::test_store_initializes_repair_loop_tables -q
```

Expected: FAIL because module does not exist.

- [ ] **Step 3: Create models**

Create `src/tradecraft/services/jue_codex_lab_models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RepairTask:
    task_id: str
    venue: str
    discipline_id: str
    source_validation_run_id: str
    status: str
    priority: str
    owner: str
    automation_hook: str
    failure_status: str
    failure_evidence: str = ""
    green_condition: dict[str, Any] = field(default_factory=dict)
    allowed_paths: list[str] = field(default_factory=list)
    blocked_paths: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Create store**

Create `src/tradecraft/services/jue_codex_lab_store.py` with schema creation using the SQL from the Database Schema section.

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from tradecraft.services.jue_codex_lab_models import RepairTask


class JueCodexLabStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS repair_tasks (
                    task_id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL,
                    discipline_id TEXT NOT NULL,
                    source_validation_run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    automation_hook TEXT NOT NULL,
                    failure_status TEXT NOT NULL,
                    failure_evidence TEXT NOT NULL DEFAULT '',
                    green_condition_json TEXT NOT NULL DEFAULT '{}',
                    allowed_paths_json TEXT NOT NULL DEFAULT '[]',
                    blocked_paths_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    due_at TEXT NOT NULL DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_run_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS repair_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    codex_thread_key TEXT NOT NULL DEFAULT '',
                    codex_thread_id TEXT NOT NULL DEFAULT '',
                    prompt_json TEXT NOT NULL DEFAULT '{}',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS patch_attempts (
                    patch_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    workspace_path TEXT NOT NULL DEFAULT '',
                    diff_summary TEXT NOT NULL DEFAULT '',
                    touched_paths_json TEXT NOT NULL DEFAULT '[]',
                    patch_text TEXT NOT NULL DEFAULT '',
                    rollback_snapshot_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verification_results (
                    verification_id TEXT PRIMARY KEY,
                    patch_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    command TEXT NOT NULL,
                    expected TEXT NOT NULL DEFAULT '',
                    output_excerpt TEXT NOT NULL DEFAULT '',
                    elapsed_sec REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deployment_events (
                    event_id TEXT PRIMARY KEY,
                    patch_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    deployed_at TEXT NOT NULL,
                    restarted_runners_json TEXT NOT NULL DEFAULT '[]',
                    post_deploy_checks_json TEXT NOT NULL DEFAULT '{}',
                    rollback_event_id TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS green_path_progress (
                    progress_id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL,
                    discipline_id TEXT NOT NULL,
                    before_status TEXT NOT NULL,
                    after_status TEXT NOT NULL,
                    before_score REAL NOT NULL DEFAULT 0,
                    after_score REAL NOT NULL DEFAULT 0,
                    validation_run_before TEXT NOT NULL DEFAULT '',
                    validation_run_after TEXT NOT NULL DEFAULT '',
                    repair_task_id TEXT NOT NULL DEFAULT '',
                    recorded_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
```

- [ ] **Step 5: Verify Task 2**

Run:

```bash
pytest tests/test_jue_codex_lab_store.py -q
python3 -m py_compile src/tradecraft/services/jue_codex_lab_models.py src/tradecraft/services/jue_codex_lab_store.py
```

Expected: PASS.

---

### Task 3: Convert Validation Work Queues into Repair Tasks

**Files:**
- Create: `src/tradecraft/services/jue_codex_repair_catalog.py`
- Modify: `src/tradecraft/services/jue_codex_lab_store.py`
- Create: `src/tradecraft/services/jue_codex_lab.py`
- Test: `tests/test_jue_codex_repair_catalog.py`
- Test: `tests/test_jue_codex_lab.py`

- [ ] **Step 1: Write failing catalog test**

Create `tests/test_jue_codex_repair_catalog.py`:

```python
from __future__ import annotations

from tradecraft.services.jue_codex_repair_catalog import repair_strategy_for


def test_catalog_maps_cost_fail_to_cost_repair_strategy() -> None:
    strategy = repair_strategy_for(
        venue="binance",
        discipline_id="cost_simulation",
        automation_hook="sync_live_performance_and_edges",
        failure_status="fail",
    )

    assert strategy["owner"] == "cost_model"
    assert "src/tradecraft/services/live_performance.py" in strategy["allowed_paths"]
    assert ".env" in strategy["blocked_paths"]
    assert "pytest tests/test_live_performance.py" in strategy["verification_commands"]
    assert strategy["green_condition"]["discipline_id"] == "cost_simulation"
    assert strategy["green_condition"]["target_statuses"] == ["pass", "warn"]
```

- [ ] **Step 2: Run failing catalog test**

Run:

```bash
pytest tests/test_jue_codex_repair_catalog.py::test_catalog_maps_cost_fail_to_cost_repair_strategy -q
```

Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement repair catalog**

Create `src/tradecraft/services/jue_codex_repair_catalog.py`:

```python
from __future__ import annotations

from typing import Any


DEFAULT_BLOCKED_PATHS = [".env", ".runtime", "src/tradecraft/services/kis.py", "src/tradecraft/services/binance.py"]


def repair_strategy_for(
    *,
    venue: str,
    discipline_id: str,
    automation_hook: str,
    failure_status: str,
) -> dict[str, Any]:
    clean_discipline = str(discipline_id or "").strip()
    if clean_discipline == "cost_simulation":
        return {
            "owner": "cost_model",
            "allowed_paths": [
                "src/tradecraft/services/live_performance.py",
                "src/tradecraft/services/binance_performance_cost.py",
                "src/tradecraft/services/kis_cost.py",
                "tests/test_live_performance.py",
                "tests/test_binance_performance_cost.py",
                "tests/test_kis_cost.py",
            ],
            "blocked_paths": DEFAULT_BLOCKED_PATHS,
            "verification_commands": [
                "pytest tests/test_live_performance.py tests/test_binance_performance_cost.py tests/test_kis_cost.py -q",
                "pytest tests/test_trading_validation.py -q",
            ],
            "green_condition": {
                "venue": venue,
                "discipline_id": clean_discipline,
                "target_statuses": ["pass", "warn"],
                "must_not_hide_failure": True,
            },
        }
    if clean_discipline in {
        "walk_forward_analysis",
        "out_of_sample_test",
        "overfit_validation",
    }:
        return {
            "owner": "pattern_lab",
            "allowed_paths": [
                "src/tradecraft/services/crypto_pattern_lab.py",
                "src/tradecraft/services/kr_equity_pattern_lab.py",
                "src/tradecraft/runtime/crypto_pattern_lab_runner.py",
                "tests/test_crypto_pattern_lab.py",
                "tests/test_kr_equity_pattern_lab.py",
            ],
            "blocked_paths": DEFAULT_BLOCKED_PATHS,
            "verification_commands": [
                "pytest tests/test_crypto_pattern_lab.py tests/test_kr_equity_pattern_lab.py tests/test_trading_validation.py -q",
            ],
            "green_condition": {
                "venue": venue,
                "discipline_id": clean_discipline,
                "target_statuses": ["pass", "warn"],
                "must_increase_validation_evidence": True,
            },
        }
    return {
        "owner": "risk_engine",
        "allowed_paths": [
            "src/tradecraft/services/trading_validation.py",
            "src/tradecraft/services/live_authority.py",
            "tests/test_trading_validation.py",
            "tests/test_live_authority.py",
        ],
        "blocked_paths": DEFAULT_BLOCKED_PATHS,
        "verification_commands": [
            "pytest tests/test_trading_validation.py tests/test_live_authority.py -q",
        ],
        "green_condition": {
            "venue": venue,
            "discipline_id": clean_discipline,
            "target_statuses": ["pass", "warn"],
            "must_preserve_trade_blocking_semantics": True,
        },
    }
```

- [ ] **Step 4: Write failing ingestion test**

Add to `tests/test_jue_codex_lab.py`:

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tradecraft.services.jue_codex_lab import JueCodexLabService
from tradecraft.services.jue_codex_lab_store import JueCodexLabStore


def _write_validation_run(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE validation_runs (
                run_id TEXT PRIMARY KEY,
                venue TEXT NOT NULL,
                strategy_revision_id TEXT NOT NULL DEFAULT '',
                computed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                total_score REAL NOT NULL DEFAULT 0,
                pass_count INTEGER NOT NULL DEFAULT 0,
                warn_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                missing_count INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL
            )
            """
        )
        payload = {
            "summary": {"readiness": "probe", "total_score": 34.21},
            "remediation_plan": {
                "work_queue": [
                    {
                        "task_id": "validation:cost_simulation:fail",
                        "discipline_id": "cost_simulation",
                        "status": "fail",
                        "priority": "p0",
                        "owner": "cost_model",
                        "automation_hook": "sync_live_performance_and_edges",
                        "evidence": "2x cost stress net negative",
                    }
                ]
            },
        }
        conn.execute(
            """
            INSERT INTO validation_runs (
                run_id, venue, computed_at, status, total_score,
                pass_count, warn_count, fail_count, missing_count, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "validation-1",
                "binance",
                "2026-07-02T12:00:00+00:00",
                "ok",
                34.21,
                4,
                5,
                10,
                0,
                json.dumps(payload, ensure_ascii=False),
            ),
        )


def test_ingest_validation_work_queue_creates_repair_task(tmp_path: Path) -> None:
    validation_db = tmp_path / "trading_validation.db"
    lab_db = tmp_path / "jue_codex_lab.db"
    _write_validation_run(validation_db)

    service = JueCodexLabService(
        store=JueCodexLabStore(lab_db),
        validation_db_path=validation_db,
    )
    result = service.ingest_validation_work_queue(venue="binance")

    assert result["created_count"] == 1
    tasks = service.store.list_tasks(status="queued")
    assert tasks[0]["task_id"] == "binance:validation:cost_simulation:fail"
    assert tasks[0]["discipline_id"] == "cost_simulation"
    assert tasks[0]["green_condition"]["target_statuses"] == ["pass", "warn"]
```

- [ ] **Step 5: Run failing ingestion test**

Run:

```bash
pytest tests/test_jue_codex_lab.py::test_ingest_validation_work_queue_creates_repair_task -q
```

Expected: FAIL because `JueCodexLabService` and store methods are missing.

- [ ] **Step 6: Implement store task methods and service ingestion**

Add to `JueCodexLabStore`:

```python
def upsert_task(self, task: RepairTask, *, now_iso: str) -> bool:
    self.initialize()
    with sqlite3.connect(self.db_path) as conn:
        existing = conn.execute(
            "SELECT task_id FROM repair_tasks WHERE task_id = ?",
            (task.task_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO repair_tasks (
                task_id, venue, discipline_id, source_validation_run_id,
                status, priority, owner, automation_hook, failure_status,
                failure_evidence, green_condition_json, allowed_paths_json,
                blocked_paths_json, created_at, updated_at, due_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status = excluded.status,
                priority = excluded.priority,
                failure_status = excluded.failure_status,
                failure_evidence = excluded.failure_evidence,
                green_condition_json = excluded.green_condition_json,
                allowed_paths_json = excluded.allowed_paths_json,
                blocked_paths_json = excluded.blocked_paths_json,
                updated_at = excluded.updated_at,
                due_at = excluded.due_at
            """,
            (
                task.task_id,
                task.venue,
                task.discipline_id,
                task.source_validation_run_id,
                task.status,
                task.priority,
                task.owner,
                task.automation_hook,
                task.failure_status,
                task.failure_evidence,
                self._json(task.green_condition),
                self._json(task.allowed_paths),
                self._json(task.blocked_paths),
                now_iso,
                now_iso,
                now_iso,
            ),
        )
    return existing is None

def list_tasks(self, *, status: str = "") -> list[dict[str, Any]]:
    self.initialize()
    params: list[Any] = []
    where = ""
    if status:
        where = "WHERE status = ?"
        params.append(status)
    with sqlite3.connect(self.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM repair_tasks {where} ORDER BY priority ASC, created_at ASC",
            params,
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["green_condition"] = json.loads(item.pop("green_condition_json") or "{}")
        item["allowed_paths"] = json.loads(item.pop("allowed_paths_json") or "[]")
        item["blocked_paths"] = json.loads(item.pop("blocked_paths_json") or "[]")
        result.append(item)
    return result
```

Create `src/tradecraft/services/jue_codex_lab.py`:

```python
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradecraft.services.jue_codex_lab_models import RepairTask
from tradecraft.services.jue_codex_lab_store import JueCodexLabStore
from tradecraft.services.jue_codex_repair_catalog import repair_strategy_for


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JueCodexLabService:
    def __init__(
        self,
        *,
        store: JueCodexLabStore,
        validation_db_path: str | Path,
    ) -> None:
        self.store = store
        self.validation_db_path = Path(validation_db_path)

    def ingest_validation_work_queue(self, *, venue: str) -> dict[str, Any]:
        payload = self._latest_validation_payload(venue=venue)
        run_id = str(payload.get("run_id") or "")
        remediation = payload.get("remediation_plan")
        work_queue = (
            remediation.get("work_queue")
            if isinstance(remediation, dict)
            and isinstance(remediation.get("work_queue"), list)
            else []
        )
        created_count = 0
        now_iso = _utc_now_iso()
        for item in work_queue:
            if not isinstance(item, dict):
                continue
            discipline_id = str(item.get("discipline_id") or "").strip()
            failure_status = str(item.get("status") or "").strip()
            automation_hook = str(item.get("automation_hook") or "").strip()
            if not discipline_id or failure_status not in {"warn", "fail", "missing"}:
                continue
            strategy = repair_strategy_for(
                venue=venue,
                discipline_id=discipline_id,
                automation_hook=automation_hook,
                failure_status=failure_status,
            )
            task = RepairTask(
                task_id=f"{venue}:{item.get('task_id') or discipline_id + ':' + failure_status}",
                venue=venue,
                discipline_id=discipline_id,
                source_validation_run_id=run_id,
                status="queued",
                priority=str(item.get("priority") or "p2"),
                owner=str(item.get("owner") or strategy["owner"]),
                automation_hook=automation_hook,
                failure_status=failure_status,
                failure_evidence=str(item.get("evidence") or item.get("runner_hint") or ""),
                green_condition=dict(strategy["green_condition"]),
                allowed_paths=list(strategy["allowed_paths"]),
                blocked_paths=list(strategy["blocked_paths"]),
            )
            if self.store.upsert_task(task, now_iso=now_iso):
                created_count += 1
        return {
            "status": "ok",
            "venue": venue,
            "source_validation_run_id": run_id,
            "created_count": created_count,
            "queued_count": len(self.store.list_tasks(status="queued")),
        }

    def _latest_validation_payload(self, *, venue: str) -> dict[str, Any]:
        with sqlite3.connect(self.validation_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT run_id, payload_json
                FROM validation_runs
                WHERE venue = ?
                ORDER BY computed_at DESC
                LIMIT 1
                """,
                (venue,),
            ).fetchone()
        if row is None:
            return {}
        payload = json.loads(str(row["payload_json"] or "{}"))
        if isinstance(payload, dict):
            payload.setdefault("run_id", row["run_id"])
            return payload
        return {"run_id": row["run_id"]}
```

- [ ] **Step 7: Verify Task 3**

Run:

```bash
pytest tests/test_jue_codex_repair_catalog.py tests/test_jue_codex_lab.py::test_ingest_validation_work_queue_creates_repair_task -q
```

Expected: PASS.

---

### Task 4: Codex Native Repair Planning Contract

**Files:**
- Modify: `src/tradecraft/services/jue_codex_lab.py`
- Test: `tests/test_jue_codex_lab.py`

- [ ] **Step 1: Write failing prompt contract test**

Add to `tests/test_jue_codex_lab.py`:

```python
def test_build_repair_prompt_requires_patch_and_verification_plan(tmp_path: Path) -> None:
    service = JueCodexLabService(
        store=JueCodexLabStore(tmp_path / "lab.db"),
        validation_db_path=tmp_path / "validation.db",
    )
    task = {
        "task_id": "binance:validation:cost_simulation:fail",
        "venue": "binance",
        "discipline_id": "cost_simulation",
        "failure_status": "fail",
        "failure_evidence": "2x cost stress net negative",
        "green_condition": {"target_statuses": ["pass", "warn"]},
        "allowed_paths": ["src/tradecraft/services/live_performance.py"],
        "blocked_paths": [".env", ".runtime"],
    }

    prompt = service.build_codex_repair_prompt(task)

    assert prompt["role"] == "jue_codex_autonomous_repair"
    assert prompt["approval_policy"] == "no_human_approval_required"
    assert prompt["failure_policy"] == "fail_loudly_no_fallback"
    assert prompt["task"]["task_id"] == task["task_id"]
    assert prompt["output_schema"]["required"] == [
        "root_cause",
        "patch_strategy",
        "patch",
        "verification_commands",
        "rollback_notes",
        "wiki_memory",
    ]
```

- [ ] **Step 2: Run failing prompt contract test**

Run:

```bash
pytest tests/test_jue_codex_lab.py::test_build_repair_prompt_requires_patch_and_verification_plan -q
```

Expected: FAIL because method is missing.

- [ ] **Step 3: Implement repair prompt builder**

Add to `JueCodexLabService`:

```python
def build_codex_repair_prompt(self, task: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "jue_codex_autonomous_repair",
        "approval_policy": "no_human_approval_required",
        "failure_policy": "fail_loudly_no_fallback",
        "objective": (
            "Turn this validation warning/failure into a verified code, data, "
            "research, or test improvement. Do not hide the metric. Make the "
            "system better or report the exact blocker."
        ),
        "task": task,
        "allowed_paths": list(task.get("allowed_paths") or []),
        "blocked_paths": list(task.get("blocked_paths") or []),
        "operational_constraints": {
            "do_not_edit_secrets": True,
            "do_not_disable_safety_gates": True,
            "do_not_convert_failures_to_fallbacks": True,
            "must_add_or_update_tests": True,
            "must_keep_runtime_restart_scope_minimal": True,
        },
        "output_schema": {
            "type": "object",
            "required": [
                "root_cause",
                "patch_strategy",
                "patch",
                "verification_commands",
                "rollback_notes",
                "wiki_memory",
            ],
        },
    }
```

- [ ] **Step 4: Verify Task 4**

Run:

```bash
pytest tests/test_jue_codex_lab.py::test_build_repair_prompt_requires_patch_and_verification_plan -q
```

Expected: PASS.

---

### Task 5: Patch Workspace and Path Guard

**Files:**
- Create: `src/tradecraft/services/jue_codex_patch_workspace.py`
- Test: `tests/test_jue_codex_verifier.py`

- [ ] **Step 1: Write failing path guard test**

Create `tests/test_jue_codex_verifier.py`:

```python
from __future__ import annotations

from tradecraft.services.jue_codex_patch_workspace import validate_patch_paths


def test_validate_patch_paths_rejects_blocked_secret_paths() -> None:
    result = validate_patch_paths(
        touched_paths=[".env", "src/tradecraft/services/live_performance.py"],
        allowed_paths=["src/tradecraft", "tests"],
        blocked_paths=[".env", ".runtime"],
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "blocked_path_touched"
    assert result["blocked_matches"] == [".env"]


def test_validate_patch_paths_accepts_allowed_source_and_tests() -> None:
    result = validate_patch_paths(
        touched_paths=[
            "src/tradecraft/services/live_performance.py",
            "tests/test_live_performance.py",
        ],
        allowed_paths=["src/tradecraft", "tests"],
        blocked_paths=[".env", ".runtime"],
    )

    assert result["status"] == "ok"
```

- [ ] **Step 2: Run failing path guard test**

Run:

```bash
pytest tests/test_jue_codex_verifier.py::test_validate_patch_paths_rejects_blocked_secret_paths tests/test_jue_codex_verifier.py::test_validate_patch_paths_accepts_allowed_source_and_tests -q
```

Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement path guard**

Create `src/tradecraft/services/jue_codex_patch_workspace.py`:

```python
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


def _matches_prefix(path: str, prefixes: list[str]) -> list[str]:
    clean = str(PurePosixPath(path))
    matches: list[str] = []
    for prefix in prefixes:
        clean_prefix = str(PurePosixPath(str(prefix)))
        if clean == clean_prefix or clean.startswith(f"{clean_prefix}/"):
            matches.append(prefix)
    return matches


def validate_patch_paths(
    *,
    touched_paths: list[str],
    allowed_paths: list[str],
    blocked_paths: list[str],
) -> dict[str, Any]:
    blocked_matches: list[str] = []
    outside_allowed: list[str] = []
    for path in touched_paths:
        blocked_matches.extend(_matches_prefix(path, blocked_paths))
        if not _matches_prefix(path, allowed_paths):
            outside_allowed.append(path)
    if blocked_matches:
        return {
            "status": "rejected",
            "reason": "blocked_path_touched",
            "blocked_matches": sorted(set(blocked_matches)),
        }
    if outside_allowed:
        return {
            "status": "rejected",
            "reason": "outside_allowed_paths",
            "outside_allowed": sorted(set(outside_allowed)),
        }
    return {"status": "ok"}
```

- [ ] **Step 4: Verify Task 5**

Run:

```bash
pytest tests/test_jue_codex_verifier.py -q
python3 -m py_compile src/tradecraft/services/jue_codex_patch_workspace.py
```

Expected: PASS.

---

### Task 6: Verification Runner

**Files:**
- Create: `src/tradecraft/services/jue_codex_verifier.py`
- Modify: `tests/test_jue_codex_verifier.py`

- [ ] **Step 1: Write failing command verifier test**

Add to `tests/test_jue_codex_verifier.py`:

```python
from tradecraft.services.jue_codex_verifier import JueCodexVerifier


def test_verifier_runs_commands_and_returns_pass(tmp_path) -> None:
    verifier = JueCodexVerifier(workdir=tmp_path)

    result = verifier.run_commands(["python3 -c 'print(123)'"])

    assert result["status"] == "pass"
    assert result["results"][0]["status"] == "pass"
    assert "123" in result["results"][0]["output_excerpt"]
```

- [ ] **Step 2: Run failing verifier test**

Run:

```bash
pytest tests/test_jue_codex_verifier.py::test_verifier_runs_commands_and_returns_pass -q
```

Expected: FAIL because class does not exist.

- [ ] **Step 3: Implement verifier**

Create `src/tradecraft/services/jue_codex_verifier.py`:

```python
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any


class JueCodexVerifier:
    def __init__(self, *, workdir: str | Path, timeout_sec: float = 300.0) -> None:
        self.workdir = Path(workdir)
        self.timeout_sec = float(timeout_sec)

    def run_commands(self, commands: list[str]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for command in commands:
            started = time.monotonic()
            proc = subprocess.run(
                ["/bin/zsh", "-lc", command],
                cwd=self.workdir,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.timeout_sec,
                check=False,
            )
            elapsed = time.monotonic() - started
            output = proc.stdout or ""
            rows.append(
                {
                    "command": command,
                    "status": "pass" if proc.returncode == 0 else "fail",
                    "returncode": proc.returncode,
                    "output_excerpt": output[-4000:],
                    "elapsed_sec": round(elapsed, 3),
                }
            )
            if proc.returncode != 0:
                return {"status": "fail", "results": rows}
        return {"status": "pass", "results": rows}
```

- [ ] **Step 4: Verify Task 6**

Run:

```bash
pytest tests/test_jue_codex_verifier.py -q
python3 -m py_compile src/tradecraft/services/jue_codex_verifier.py
```

Expected: PASS.

---

### Task 7: Autonomous Run Once Pipeline

**Files:**
- Modify: `src/tradecraft/services/jue_codex_lab.py`
- Modify: `src/tradecraft/services/jue_codex_lab_store.py`
- Test: `tests/test_jue_codex_lab.py`

- [ ] **Step 1: Write failing no-fallback run-once test**

Add to `tests/test_jue_codex_lab.py`:

```python
class FakeCodexRuntime:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def complete_json(self, payload):
        self.requests.append(payload)
        return self.response


def test_run_once_records_failed_codex_response_without_fallback(tmp_path: Path) -> None:
    validation_db = tmp_path / "trading_validation.db"
    lab_db = tmp_path / "jue_codex_lab.db"
    _write_validation_run(validation_db)
    service = JueCodexLabService(
        store=JueCodexLabStore(lab_db),
        validation_db_path=validation_db,
        codex_runtime=FakeCodexRuntime({"root_cause": "missing patch"}),
    )
    service.ingest_validation_work_queue(venue="binance")

    result = service.run_once(max_tasks=1)

    assert result["status"] == "error"
    assert result["processed_count"] == 1
    assert result["failed_count"] == 1
    assert "patch_missing" in result["errors"][0]
```

- [ ] **Step 2: Run failing no-fallback test**

Run:

```bash
pytest tests/test_jue_codex_lab.py::test_run_once_records_failed_codex_response_without_fallback -q
```

Expected: FAIL because pipeline is missing.

- [ ] **Step 3: Implement minimal run-once pipeline**

Extend `JueCodexLabService.__init__` with optional `codex_runtime` and implement:

```python
def run_once(self, *, max_tasks: int = 1) -> dict[str, Any]:
    tasks = self.store.list_tasks(status="queued")[:max(int(max_tasks), 0)]
    errors: list[str] = []
    processed = 0
    for task in tasks:
        processed += 1
        prompt = self.build_codex_repair_prompt(task)
        response = self.codex_runtime.complete_json(prompt) if self.codex_runtime else {}
        if not isinstance(response, dict) or not response.get("patch"):
            errors.append(f"{task['task_id']}:patch_missing")
            self.store.mark_task_status(task["task_id"], status="failed")
            continue
        errors.append(f"{task['task_id']}:verification_not_implemented")
        self.store.mark_task_status(task["task_id"], status="failed")
    return {
        "status": "error" if errors else "ok",
        "processed_count": processed,
        "failed_count": len(errors),
        "errors": errors,
    }
```

Add `mark_task_status` to store:

```python
def mark_task_status(self, task_id: str, *, status: str) -> None:
    self.initialize()
    with sqlite3.connect(self.db_path) as conn:
        conn.execute(
            """
            UPDATE repair_tasks
            SET status = ?, updated_at = datetime('now')
            WHERE task_id = ?
            """,
            (status, task_id),
        )
```

- [ ] **Step 4: Verify Task 7**

Run:

```bash
pytest tests/test_jue_codex_lab.py::test_run_once_records_failed_codex_response_without_fallback -q
```

Expected: PASS.

---

### Task 8: Verified Auto-Apply Deployment

**Files:**
- Modify: `src/tradecraft/services/jue_codex_lab.py`
- Modify: `src/tradecraft/services/jue_codex_lab_store.py`
- Test: `tests/test_jue_codex_lab.py`

- [ ] **Step 1: Write failing verified patch deployment test**

Add to `tests/test_jue_codex_lab.py`:

```python
def test_run_once_auto_applies_verified_patch_and_records_deployment(tmp_path: Path) -> None:
    validation_db = tmp_path / "trading_validation.db"
    lab_db = tmp_path / "jue_codex_lab.db"
    target = tmp_path / "src" / "tradecraft" / "services" / "live_performance.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n")
    _write_validation_run(validation_db)
    service = JueCodexLabService(
        store=JueCodexLabStore(lab_db),
        validation_db_path=validation_db,
        repo_root=tmp_path,
        codex_runtime=FakeCodexRuntime(
            {
                "root_cause": "test repair",
                "patch": {
                    "touched_paths": ["src/tradecraft/services/live_performance.py"],
                    "files": [
                        {
                            "path": "src/tradecraft/services/live_performance.py",
                            "content": "VALUE = 2\n",
                        }
                    ],
                },
                "verification_commands": ["python3 -m py_compile src/tradecraft/services/live_performance.py"],
                "wiki_memory": {"summary": "cost repair tested"},
            }
        ),
    )
    service.ingest_validation_work_queue(venue="binance")

    result = service.run_once(max_tasks=1)

    assert result["status"] == "ok"
    assert target.read_text() == "VALUE = 2\n"
    deployments = service.store.list_deployment_events()
    assert deployments[0]["status"] == "deployed"
```

- [ ] **Step 2: Run failing deployment test**

Run:

```bash
pytest tests/test_jue_codex_lab.py::test_run_once_auto_applies_verified_patch_and_records_deployment -q
```

Expected: FAIL because auto-apply is not implemented.

- [ ] **Step 3: Implement verified auto-apply**

Implement in `JueCodexLabService.run_once`:

1. Validate `response["patch"]["touched_paths"]` with `validate_patch_paths`.
2. Write each `{"path", "content"}` file only if path validation passes.
3. Run `JueCodexVerifier.run_commands(response["verification_commands"])`.
4. If verification passes, record patch attempt and deployment event as `deployed`.
5. If verification fails, keep modified files rolled back from snapshot and record `verification_failed`.

Add concrete store helpers:

- `record_deployment_event(self, *, patch_id: str, status: str, deployed_at: str, restarted_runners: list[str] | None = None, post_deploy_checks: dict[str, Any] | None = None, error_message: str = "") -> str`
- `list_deployment_events(self) -> list[dict[str, Any]]`

- [ ] **Step 4: Verify Task 8**

Run:

```bash
pytest tests/test_jue_codex_lab.py::test_run_once_auto_applies_verified_patch_and_records_deployment -q
```

Expected: PASS.

---

### Task 9: Green Path Progress Projection

**Files:**
- Modify: `src/tradecraft/services/jue_codex_lab.py`
- Modify: `src/tradecraft/services/jue_codex_lab_store.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Test: `tests/test_jue_codex_lab.py`
- Test: `tests/test_jue_wiki.py`

- [ ] **Step 1: Write failing green progress test**

Add to `tests/test_jue_codex_lab.py`:

```python
def test_record_green_path_progress_when_status_improves(tmp_path: Path) -> None:
    service = JueCodexLabService(
        store=JueCodexLabStore(tmp_path / "lab.db"),
        validation_db_path=tmp_path / "validation.db",
    )

    service.record_green_path_progress(
        venue="binance",
        discipline_id="cost_simulation",
        before={"status": "fail", "score": 34.21, "run_id": "before"},
        after={"status": "warn", "score": 44.0, "run_id": "after"},
        repair_task_id="binance:validation:cost_simulation:fail",
    )

    rows = service.store.list_green_path_progress()
    assert rows[0]["before_status"] == "fail"
    assert rows[0]["after_status"] == "warn"
    assert rows[0]["after_score"] == 44.0
```

- [ ] **Step 2: Run failing progress test**

Run:

```bash
pytest tests/test_jue_codex_lab.py::test_record_green_path_progress_when_status_improves -q
```

Expected: FAIL.

- [ ] **Step 3: Implement progress recording**

Add store insert/list methods for `green_path_progress`. Add service method:

```python
def record_green_path_progress(
    self,
    *,
    venue: str,
    discipline_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
    repair_task_id: str,
) -> dict[str, Any]:
    return self.store.record_green_path_progress(
        venue=venue,
        discipline_id=discipline_id,
        before_status=str(before.get("status") or ""),
        after_status=str(after.get("status") or ""),
        before_score=float(before.get("score") or 0),
        after_score=float(after.get("score") or 0),
        validation_run_before=str(before.get("run_id") or ""),
        validation_run_after=str(after.get("run_id") or ""),
        repair_task_id=repair_task_id,
    )
```

- [ ] **Step 4: Write failing Wiki page test**

Add to `tests/test_jue_wiki.py`:

```python
def test_rebuild_writes_codex_lab_green_path_pages(tmp_path: Path) -> None:
    lab_db = tmp_path / "jue_codex_lab.db"
    from tradecraft.services.jue_codex_lab_store import JueCodexLabStore

    store = JueCodexLabStore(lab_db)
    store.initialize()
    store.record_green_path_progress(
        venue="binance",
        discipline_id="cost_simulation",
        before_status="fail",
        after_status="warn",
        before_score=34.21,
        after_score=44.0,
        validation_run_before="before",
        validation_run_after="after",
        repair_task_id="binance:validation:cost_simulation:fail",
    )

    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "wiki",
            db_path=tmp_path / "wiki" / "wiki.db",
            jue_codex_lab_db_path=lab_db,
        )
    )
    result = service.rebuild(scope="binance", force=True)
    page = service.read_page("binance.ops.codex_lab_green_path")

    assert result["status"] == "ok"
    assert page["status"] == "ok"
    assert "cost_simulation" in page["content"]
    assert "fail -> warn" in page["content"]
```

- [ ] **Step 5: Implement Wiki config and page**

Add `jue_codex_lab_db_path: Path | None = None` to `JueWikiConfig`.

Add `_rebuild_codex_lab_green_path_page(scope: str)` and call it in rebuild after trading validation page. It should read `green_path_progress`, summarize status transitions, and write `scope.ops.codex_lab_green_path`.

- [ ] **Step 6: Verify Task 9**

Run:

```bash
pytest tests/test_jue_codex_lab.py::test_record_green_path_progress_when_status_improves tests/test_jue_wiki.py::test_rebuild_writes_codex_lab_green_path_pages -q
```

Expected: PASS.

---

### Task 10: API and Readiness Integration

**Files:**
- Create: `src/tradecraft/api/jue_codex_lab_router.py`
- Modify: `src/tradecraft/main.py`
- Modify: `src/tradecraft/runtime/process_status.py`
- Test: `tests/test_jue_codex_lab_api.py`
- Test: `tests/test_process_status.py`

- [ ] **Step 1: Write failing API status test**

Create `tests/test_jue_codex_lab_api.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.jue_codex_lab_router import create_jue_codex_lab_router


class FakeLab:
    def status(self):
        return {"status": "ok", "queued_count": 2}

    def run_once(self, max_tasks=1):
        return {"status": "ok", "processed_count": max_tasks}


def test_jue_codex_lab_status_endpoint() -> None:
    app = FastAPI()
    app.include_router(create_jue_codex_lab_router(lab_provider=lambda: FakeLab()))

    response = TestClient(app).get("/api/jue/codex-lab/status")

    assert response.status_code == 200
    assert response.json()["queued_count"] == 2
```

- [ ] **Step 2: Run failing API test**

Run:

```bash
pytest tests/test_jue_codex_lab_api.py::test_jue_codex_lab_status_endpoint -q
```

Expected: FAIL because router does not exist.

- [ ] **Step 3: Implement router**

Create `src/tradecraft/api/jue_codex_lab_router.py`:

```python
from __future__ import annotations

from typing import Callable, Any

from fastapi import APIRouter


def create_jue_codex_lab_router(*, lab_provider: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/jue/codex-lab", tags=["jue-codex-lab"])

    @router.get("/status")
    def status() -> dict[str, Any]:
        return lab_provider().status()

    @router.post("/run-once")
    def run_once(max_tasks: int = 1) -> dict[str, Any]:
        return lab_provider().run_once(max_tasks=max_tasks)

    return router
```

- [ ] **Step 4: Add service status**

Add to `JueCodexLabService`:

```python
def status(self) -> dict[str, Any]:
    queued = self.store.list_tasks(status="queued")
    failed = self.store.list_tasks(status="failed")
    return {
        "status": "ok",
        "db_path": str(self.store.db_path),
        "queued_count": len(queued),
        "failed_count": len(failed),
    }
```

- [ ] **Step 5: Register in main app**

In `src/tradecraft/main.py`, instantiate `JueCodexLabService` and include router:

```python
from tradecraft.api.jue_codex_lab_router import create_jue_codex_lab_router
from tradecraft.services.jue_codex_lab import JueCodexLabService
from tradecraft.services.jue_codex_lab_store import JueCodexLabStore

jue_codex_lab_service = JueCodexLabService(
    store=JueCodexLabStore(settings.jue_codex_lab_db_path),
    validation_db_path=settings.trading_validation_db_path,
)

app.include_router(
    create_jue_codex_lab_router(lab_provider=lambda: jue_codex_lab_service)
)
```

Protect these endpoints with the existing admin auth dependency if the route group pattern requires it.

- [ ] **Step 6: Add process status spec**

Add `jue_codex_lab` to `RUNNER_RESTART_SPECS` in `src/tradecraft/runtime/process_status.py`:

```python
"jue_codex_lab": RunnerRestartSpec(
    key="jue_codex_lab",
    pid_file="tradecraft-jue-codex-lab.pid",
    session_names=("tradecraft-jue-codex-lab",),
    primary_session="tradecraft-jue-codex-lab",
    command=".venv/bin/tradecraft-jue-codex-lab",
    log_path=".runtime/jue_codex_lab.log",
)
```

- [ ] **Step 7: Verify Task 10**

Run:

```bash
pytest tests/test_jue_codex_lab_api.py tests/test_process_status.py -q
```

Expected: PASS.

---

### Task 11: Runner Loop

**Files:**
- Create: `src/tradecraft/runtime/jue_codex_lab_runner.py`
- Test: `tests/test_jue_codex_lab_runner.py`

- [ ] **Step 1: Write failing runner test**

Create `tests/test_jue_codex_lab_runner.py`:

```python
from __future__ import annotations

from tradecraft.runtime.jue_codex_lab_runner import run_jue_codex_lab_cycle


class FakeLab:
    def __init__(self):
        self.calls = []

    def ingest_validation_work_queue(self, venue):
        self.calls.append(("ingest", venue))
        return {"created_count": 1}

    def run_once(self, max_tasks):
        self.calls.append(("run_once", max_tasks))
        return {"status": "ok", "processed_count": max_tasks}


def test_runner_cycle_ingests_both_venues_then_runs_repairs() -> None:
    lab = FakeLab()

    result = run_jue_codex_lab_cycle(lab=lab, max_tasks=2)

    assert result["status"] == "ok"
    assert lab.calls == [
        ("ingest", "kis"),
        ("ingest", "binance"),
        ("run_once", 2),
    ]
```

- [ ] **Step 2: Run failing runner test**

Run:

```bash
pytest tests/test_jue_codex_lab_runner.py::test_runner_cycle_ingests_both_venues_then_runs_repairs -q
```

Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement runner**

Create `src/tradecraft/runtime/jue_codex_lab_runner.py`:

```python
from __future__ import annotations

import logging
import time

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.services.jue_codex_lab import JueCodexLabService
from tradecraft.services.jue_codex_lab_store import JueCodexLabStore

logger = logging.getLogger(__name__)


def run_jue_codex_lab_cycle(*, lab: JueCodexLabService, max_tasks: int) -> dict:
    for venue in ("kis", "binance"):
        lab.ingest_validation_work_queue(venue=venue)
    result = lab.run_once(max_tasks=max_tasks)
    return {"status": result.get("status", "ok"), "repair": result}


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    write_current_runner_pid("jue_codex_lab")
    settings = AppSettings()
    lab = JueCodexLabService(
        store=JueCodexLabStore(settings.jue_codex_lab_db_path),
        validation_db_path=settings.trading_validation_db_path,
    )
    interval = max(int(settings.jue_codex_lab_interval_sec), 300)
    while True:
        if settings.jue_codex_lab_enabled:
            result = run_jue_codex_lab_cycle(
                lab=lab,
                max_tasks=int(settings.jue_codex_lab_max_tasks_per_cycle),
            )
            logger.info("jue codex lab cycle status=%s", result.get("status"))
        time.sleep(interval)
```

- [ ] **Step 4: Verify Task 11**

Run:

```bash
pytest tests/test_jue_codex_lab_runner.py -q
python3 -m py_compile src/tradecraft/runtime/jue_codex_lab_runner.py
```

Expected: PASS.

---

### Task 12: Telegram and Error Notifications

**Files:**
- Modify: `src/tradecraft/services/telegram_cli.py`
- Test: `tests/test_telegram_cli.py`

- [ ] **Step 1: Write failing Telegram command test**

Add to `tests/test_telegram_cli.py`:

```python
def test_codex_lab_command_shows_repair_queue() -> None:
    from tradecraft.services.telegram_cli import TelegramCommandRouter

    router = TelegramCommandRouter(
        jue_codex_lab_status=lambda: {
            "status": "ok",
            "queued_count": 3,
            "failed_count": 1,
        }
    )

    response = router.handle_text("/codexlab")

    assert "Codex Lab" in response["text"]
    assert "queued=3" in response["text"]
    assert "failed=1" in response["text"]
```

- [ ] **Step 2: Run failing Telegram test**

Run:

```bash
pytest tests/test_telegram_cli.py::test_codex_lab_command_shows_repair_queue -q
```

Expected: FAIL because command dependency is missing.

- [ ] **Step 3: Implement command**

Follow existing `TelegramCommandRouter` dependency injection style. Add optional `jue_codex_lab_status` callable and handle `/codexlab`:

```python
if command == "/codexlab":
    payload = self.jue_codex_lab_status() if self.jue_codex_lab_status else {}
    return {
        "text": (
            "Codex Lab "
            f"status={payload.get('status', 'unknown')} "
            f"queued={payload.get('queued_count', 0)} "
            f"failed={payload.get('failed_count', 0)}"
        )
    }
```

- [ ] **Step 4: Verify Task 12**

Run:

```bash
pytest tests/test_telegram_cli.py::test_codex_lab_command_shows_repair_queue -q
```

Expected: PASS.

---

### Task 13: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
pytest \
  tests/test_jue_codex_lab_store.py \
  tests/test_jue_codex_repair_catalog.py \
  tests/test_jue_codex_lab.py \
  tests/test_jue_codex_verifier.py \
  tests/test_jue_codex_lab_runner.py \
  tests/test_jue_codex_lab_api.py \
  tests/test_jue_wiki.py \
  tests/test_api_smoke.py \
  tests/test_process_status.py \
  tests/test_telegram_cli.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run static checks**

Run:

```bash
python3 -m py_compile \
  src/tradecraft/services/jue_codex_lab.py \
  src/tradecraft/services/jue_codex_lab_models.py \
  src/tradecraft/services/jue_codex_lab_store.py \
  src/tradecraft/services/jue_codex_patch_workspace.py \
  src/tradecraft/services/jue_codex_repair_catalog.py \
  src/tradecraft/services/jue_codex_verifier.py \
  src/tradecraft/runtime/jue_codex_lab_runner.py \
  src/tradecraft/api/jue_codex_lab_router.py
```

Expected: no output.

- [ ] **Step 3: Run diff hygiene**

Run:

```bash
git diff --check -- \
  src/tradecraft/services/jue_codex_lab.py \
  src/tradecraft/services/jue_codex_lab_models.py \
  src/tradecraft/services/jue_codex_lab_store.py \
  src/tradecraft/services/jue_codex_patch_workspace.py \
  src/tradecraft/services/jue_codex_repair_catalog.py \
  src/tradecraft/services/jue_codex_verifier.py \
  src/tradecraft/runtime/jue_codex_lab_runner.py \
  src/tradecraft/api/jue_codex_lab_router.py \
  src/tradecraft/config.py \
  src/tradecraft/main.py \
  src/tradecraft/runtime/process_status.py \
  tests/test_jue_codex_lab_store.py \
  tests/test_jue_codex_repair_catalog.py \
  tests/test_jue_codex_lab.py \
  tests/test_jue_codex_verifier.py \
  tests/test_jue_codex_lab_runner.py \
  tests/test_jue_codex_lab_api.py
```

Expected: no output.

- [ ] **Step 4: Start runner and verify readiness**

Run:

```bash
python3 - <<'PY'
from tradecraft.runtime.process_status import restart_runner_processes
print(restart_runner_processes(["jue_codex_lab"], delay_sec=0.2))
PY
sleep 5
curl -sS http://127.0.0.1:18080/api/health
```

Expected: health returns a successful `status=ok` payload and readiness includes `jue_codex_lab` as running after control has been updated and restarted.

## Success Criteria

- Every latest validation warn/fail creates or updates a concrete repair task.
- Codex native receives an audit-ready repair prompt with allowed paths, blocked paths, green condition, and no-fallback policy.
- Verified patches can be applied without human approval.
- Failed Codex attempts remain visible as failures; no deterministic fallback hides them.
- Green path progress is recorded when validation status improves.
- Jue Wiki shows repair queue and green-path progress.
- Readiness shows whether the self-improvement loop is running.
- Telegram can report repair queue health.

## Self-Review

- Spec coverage: Covers autonomous ingestion, Codex native repair planning, patch validation, auto-apply, green progress, Wiki, API, runner, Telegram, and verification.
- Unresolved-marker scan: No unfinished marker text is present. Each task has concrete files, test commands, and expected outcomes.
- Type consistency: `RepairTask`, `JueCodexLabStore`, `JueCodexLabService`, and runner names are consistent across tasks.
- Scope check: This is a large but coherent subsystem. It should be implemented task-by-task with subagent-driven development, not mixed with unrelated trading strategy changes.
