# Codex Native Absorption V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make HERMES/Jue use Codex native capabilities as first-class runtime infrastructure: persistent threads, instruction hierarchy, contract-backed output schemas, account/model readiness, and turn-level provenance.

**Architecture:** Keep the trading engines and safety gates unchanged, and deepen only the Codex-native runtime boundary. `CodexNativeRuntime` remains the single caller-facing API, while new helper modules provide thread persistence, contract schema loading, instruction pack construction, and readiness checks.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, `openai_codex.AsyncCodex`, static HTML/CSS/JS, pytest.

---

## Why This Is The Right Next Absorption

The current native migration removed the old bridge and routes all LLM work through `src/tradecraft/services/codex_native.py`. That gives us a clean foundation, but the current runtime still starts a fresh ephemeral Codex thread for almost every call. The next layer should make Codex behave less like a stateless completion endpoint and more like Jue's operating cortex.

These are the top five priority absorptions:

1. **Persistent native threads:** KIS Jue, Binance Jue, memory/reflection, research, and helper Q&A get scoped Codex threads instead of always-new ephemeral threads.
2. **Native instruction hierarchy:** Jue persona, venue rules, language policy, safety gates, and workflow authority move into `developer_instructions` / `base_instructions`; per-run payloads contain evidence and task data.
3. **Contract-backed output schemas:** `src/tradecraft/jue/contracts/*.json` becomes the single source of truth for native `output_schema`, instead of ad-hoc example schemas copied into prompts.
4. **SDK readiness API/UI:** The app shows Codex account state, model availability, thread mode, per-component model mapping, and last native runtime error.
5. **Thread read + turn metadata:** Every native turn stores thread id, schema hash, skill refs, model, effort, status, usage, and optional `thread.read()` snapshots for debugging and replay.

This does **not** grant Codex direct order execution. Live trading remains behind KIS/Binance adapters, block rules, reconciliation, kill switch, cash/position checks, and existing admin auth.

## Current Code Facts

- Native runtime file: `src/tradecraft/services/codex_native.py`
- Active native calls use `AsyncCodex`, `CodexConfig`, `Sandbox.read_only`, `ApprovalMode.deny_all`, `SkillInput`, and `TextInput`.
- Current `_call_sdk()` starts `thread_start(... ephemeral=True ...)` for every call.
- Jue workflow registry already exists in `src/tradecraft/services/jue_skill_registry.py`.
- Jue contracts already exist in `src/tradecraft/jue/contracts/*.json`.
- Settings live in `src/tradecraft/config.py`.
- Readiness API is `GET /api/ops/readiness` in `src/tradecraft/main.py`.
- Settings UI and Jue workflow UI live in `src/tradecraft/web/static/app.js`.
- Existing focused tests include `tests/test_codex_native.py`, `tests/test_config.py`, `tests/test_api_smoke.py`, `tests/test_jue_skill_registry.py`, and `tests/test_jue_workflow_manifests.py`.

## File Structure

- Create: `src/tradecraft/services/codex_native_store.py`
  - SQLite repository for thread registry, turn metadata, account checks, model checks.
- Create: `src/tradecraft/services/codex_contracts.py`
  - Converts Jue contract assets into native JSON Schema and validates contract ids.
- Create: `src/tradecraft/services/codex_instructions.py`
  - Builds `base_instructions` and `developer_instructions` from component, workflow pack, language policy, and safety gates.
- Modify: `src/tradecraft/services/codex_native.py`
  - Uses store, instruction builder, contract loader, readiness methods, thread start/resume/read/compact.
- Modify: `src/tradecraft/config.py`
  - Adds native thread/readiness env settings.
- Modify: `src/tradecraft/main.py`
  - Wires config into runtimes and extends `/api/ops/readiness`; adds `/api/codex/native/status`.
- Modify: `src/tradecraft/services/settings_catalog.py`
  - Exposes safe runtime settings in Settings UI.
- Modify: `src/tradecraft/web/static/app.js`
  - Adds Codex native readiness panel and status badges.
- Modify: `src/tradecraft/web/static/style.css`
  - Adds compact status styles only if existing classes are insufficient.
- Modify: `tests/test_codex_native.py`
  - Main unit coverage for threads, instructions, contracts, readiness, turn metadata.
- Modify: `tests/test_config.py`
  - Env parsing coverage.
- Modify: `tests/test_api_smoke.py`
  - Readiness/status endpoint coverage.
- Modify: `tests/test_static_ui.py`
  - Static UI string coverage.
- Modify: `.env.example`
  - Documents new env keys without secrets.

---

## Task 1: Native Thread Store

**Files:**
- Create: `src/tradecraft/services/codex_native_store.py`
- Test: `tests/test_codex_native.py`

- [ ] **Step 1: Write failing tests for thread upsert, lookup, and turn recording**

Add these tests to `tests/test_codex_native.py`:

```python
def test_codex_native_store_records_and_resumes_thread(tmp_path: Path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "codex_native_threads.db"
    store = CodexNativeStore(str(db_path))

    store.upsert_thread(
        thread_key="kis:kis_intraday_manager:2026-06-03",
        thread_id="thread_123",
        component="kis_block_manager",
        workflow_id="kis_intraday_manager",
        model="gpt-5.5",
        reasoning_effort="xhigh",
        status="active",
        metadata={"scope": "daily"},
    )

    row = store.get_active_thread("kis:kis_intraday_manager:2026-06-03")
    assert row is not None
    assert row["thread_id"] == "thread_123"
    assert row["component"] == "kis_block_manager"
    assert row["workflow_id"] == "kis_intraday_manager"
    assert row["metadata"]["scope"] == "daily"


def test_codex_native_store_records_turn_metadata(tmp_path: Path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "codex_native_threads.db"
    store = CodexNativeStore(str(db_path))

    run_id = store.record_turn(
        thread_key="binance:binance_cycle:2026-06-03T14",
        thread_id="thread_binance",
        component="binance_block_manager",
        operation="manager_cycle",
        workflow_id="binance_cycle",
        model="gpt-5.3-codex-spark",
        reasoning_effort="xhigh",
        status="ok",
        latency_ms=4312,
        input_hash="abc",
        output_schema_hash="def",
        skill_refs=[{"name": "jue-binance-trading", "path": "/tmp/SKILL.md"}],
        usage={"total_tokens": 1200},
        error_message="",
        result={"ok": True},
        thread_read=None,
    )

    rows = store.list_recent_turns(limit=5)
    assert rows[0]["run_id"] == run_id
    assert rows[0]["component"] == "binance_block_manager"
    assert rows[0]["usage"]["total_tokens"] == 1200
    assert rows[0]["skill_refs"][0]["name"] == "jue-binance-trading"
```

- [ ] **Step 2: Run tests and confirm import failure**

Run:

```bash
pytest tests/test_codex_native.py::test_codex_native_store_records_and_resumes_thread tests/test_codex_native.py::test_codex_native_store_records_turn_metadata -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tradecraft.services.codex_native_store'`.

- [ ] **Step 3: Implement the store**

Create `src/tradecraft/services/codex_native_store.py`:

```python
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class CodexNativeStore:
    def __init__(self, path: str) -> None:
        self.path = str(path or ".runtime/codex_native_threads.db")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_threads (
                    thread_key TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    component TEXT NOT NULL,
                    workflow_id TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL,
                    reasoning_effort TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    resumed_at TEXT NOT NULL,
                    compacted_at TEXT NOT NULL DEFAULT '',
                    archived_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_turns (
                    run_id TEXT PRIMARY KEY,
                    thread_key TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    component TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    workflow_id TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL,
                    reasoning_effort TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    input_hash TEXT NOT NULL DEFAULT '',
                    output_schema_hash TEXT NOT NULL DEFAULT '',
                    skill_refs_json TEXT NOT NULL DEFAULT '[]',
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    thread_read_json TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_account_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL,
                    account_label TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    checked_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS codex_model_checks (
                    model TEXT PRIMARY KEY,
                    available INTEGER NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    checked_at TEXT NOT NULL
                )
                """
            )

    def upsert_thread(
        self,
        *,
        thread_key: str,
        thread_id: str,
        component: str,
        workflow_id: str,
        model: str,
        reasoning_effort: str,
        status: str,
        metadata: dict[str, Any],
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_threads (
                    thread_key, thread_id, component, workflow_id, model,
                    reasoning_effort, status, metadata_json, created_at, resumed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_key) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    component = excluded.component,
                    workflow_id = excluded.workflow_id,
                    model = excluded.model,
                    reasoning_effort = excluded.reasoning_effort,
                    status = excluded.status,
                    metadata_json = excluded.metadata_json,
                    resumed_at = excluded.resumed_at
                """,
                (
                    thread_key,
                    thread_id,
                    component,
                    workflow_id,
                    model,
                    reasoning_effort,
                    status,
                    _json_dumps(metadata),
                    now,
                    now,
                ),
            )

    def get_active_thread(self, thread_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM codex_threads
                WHERE thread_key = ? AND status = 'active'
                """,
                (thread_key,),
            ).fetchone()
        if row is None:
            return None
        return self._thread_row(row)

    def mark_thread_compacted(self, thread_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE codex_threads SET compacted_at = ? WHERE thread_key = ?",
                (_utc_now(), thread_key),
            )

    def archive_thread(self, thread_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE codex_threads
                SET status = 'archived', archived_at = ?
                WHERE thread_key = ?
                """,
                (_utc_now(), thread_key),
            )

    def record_turn(
        self,
        *,
        thread_key: str,
        thread_id: str,
        component: str,
        operation: str,
        workflow_id: str,
        model: str,
        reasoning_effort: str,
        status: str,
        latency_ms: int,
        input_hash: str,
        output_schema_hash: str,
        skill_refs: list[dict[str, Any]],
        usage: dict[str, Any] | None,
        error_message: str,
        result: dict[str, Any] | None,
        thread_read: dict[str, Any] | None,
    ) -> str:
        run_id = uuid.uuid4().hex
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_turns (
                    run_id, thread_key, thread_id, component, operation, workflow_id,
                    model, reasoning_effort, status, latency_ms, input_hash,
                    output_schema_hash, skill_refs_json, usage_json, error_message,
                    result_json, thread_read_json, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    thread_key,
                    thread_id,
                    component,
                    operation,
                    workflow_id,
                    model,
                    reasoning_effort,
                    status,
                    max(int(latency_ms), 0),
                    input_hash,
                    output_schema_hash,
                    _json_dumps(skill_refs),
                    _json_dumps(usage or {}),
                    str(error_message or "")[:2000],
                    _json_dumps(result or {}),
                    _json_dumps(thread_read) if thread_read else "",
                    now,
                    now,
                ),
            )
        return run_id

    def list_recent_turns(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(min(int(limit), 200), 1)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM codex_turns ORDER BY finished_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self._turn_row(row) for row in rows]

    def count_turns_for_thread(self, thread_key: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM codex_turns WHERE thread_key = ?",
                (thread_key,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def record_account_check(
        self,
        *,
        status: str,
        account_label: str,
        detail: dict[str, Any],
        error_message: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_account_checks (
                    status, account_label, detail_json, error_message, checked_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    status,
                    account_label,
                    _json_dumps(detail),
                    str(error_message or "")[:2000],
                    _utc_now(),
                ),
            )

    def record_model_check(
        self,
        *,
        model: str,
        available: bool,
        detail: dict[str, Any],
        error_message: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO codex_model_checks (
                    model, available, detail_json, error_message, checked_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(model) DO UPDATE SET
                    available = excluded.available,
                    detail_json = excluded.detail_json,
                    error_message = excluded.error_message,
                    checked_at = excluded.checked_at
                """,
                (
                    model,
                    1 if available else 0,
                    _json_dumps(detail),
                    str(error_message or "")[:2000],
                    _utc_now(),
                ),
            )

    def latest_account_check(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM codex_account_checks ORDER BY checked_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "status": row["status"],
            "account_label": row["account_label"],
            "detail": _json_loads(row["detail_json"], {}),
            "error_message": row["error_message"],
            "checked_at": row["checked_at"],
        }

    def list_model_checks(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM codex_model_checks ORDER BY model"
            ).fetchall()
        return [
            {
                "model": row["model"],
                "available": bool(row["available"]),
                "detail": _json_loads(row["detail_json"], {}),
                "error_message": row["error_message"],
                "checked_at": row["checked_at"],
            }
            for row in rows
        ]

    def _thread_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "thread_key": row["thread_key"],
            "thread_id": row["thread_id"],
            "component": row["component"],
            "workflow_id": row["workflow_id"],
            "model": row["model"],
            "reasoning_effort": row["reasoning_effort"],
            "status": row["status"],
            "metadata": _json_loads(row["metadata_json"], {}),
            "created_at": row["created_at"],
            "resumed_at": row["resumed_at"],
            "compacted_at": row["compacted_at"],
            "archived_at": row["archived_at"],
        }

    def _turn_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "thread_key": row["thread_key"],
            "thread_id": row["thread_id"],
            "component": row["component"],
            "operation": row["operation"],
            "workflow_id": row["workflow_id"],
            "model": row["model"],
            "reasoning_effort": row["reasoning_effort"],
            "status": row["status"],
            "latency_ms": row["latency_ms"],
            "input_hash": row["input_hash"],
            "output_schema_hash": row["output_schema_hash"],
            "skill_refs": _json_loads(row["skill_refs_json"], []),
            "usage": _json_loads(row["usage_json"], {}),
            "error_message": row["error_message"],
            "result": _json_loads(row["result_json"], {}),
            "thread_read": _json_loads(row["thread_read_json"], None),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }
```

- [ ] **Step 4: Run store tests**

Run:

```bash
pytest tests/test_codex_native.py::test_codex_native_store_records_and_resumes_thread tests/test_codex_native.py::test_codex_native_store_records_turn_metadata -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/services/codex_native_store.py tests/test_codex_native.py
git commit -m "feat: add codex native thread store"
```

---

## Task 2: Runtime Config For Persistent Threads

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `src/tradecraft/services/codex_native.py`
- Modify: `src/tradecraft/main.py`
- Modify: `src/tradecraft/services/settings_catalog.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write config tests**

Add to `tests/test_config.py`:

```python
def test_codex_native_thread_settings(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_THREAD_MODE", "daily")
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_THREAD_DB_PATH", ".runtime/test_threads.db")
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_COMPACT_AFTER_TURNS", "7")
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_READ_TURNS", "true")
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_ACCOUNT_CHECK_INTERVAL_SEC", "120")
    monkeypatch.setenv("TRADECRAFT_CODEX_NATIVE_MODEL_CHECK_INTERVAL_SEC", "300")

    from tradecraft.config import AppSettings

    settings = AppSettings()
    assert settings.codex_native_thread_mode == "daily"
    assert settings.codex_native_thread_db_path == ".runtime/test_threads.db"
    assert settings.codex_native_compact_after_turns == 7
    assert settings.codex_native_read_turns is True
    assert settings.codex_native_account_check_interval_sec == 120
    assert settings.codex_native_model_check_interval_sec == 300
```

- [ ] **Step 2: Run config test and confirm missing fields**

Run:

```bash
pytest tests/test_config.py::test_codex_native_thread_settings -q
```

Expected: FAIL because `AppSettings` does not yet expose these fields.

- [ ] **Step 3: Add settings**

In `src/tradecraft/config.py`, add these fields near existing `codex_runtime_*` fields:

```python
    codex_native_thread_mode: str = Field(
        default="daily",
        alias="TRADECRAFT_CODEX_NATIVE_THREAD_MODE",
    )
    codex_native_thread_db_path: str = Field(
        default=".runtime/codex_native_threads.db",
        alias="TRADECRAFT_CODEX_NATIVE_THREAD_DB_PATH",
    )
    codex_native_compact_after_turns: int = Field(
        default=8,
        alias="TRADECRAFT_CODEX_NATIVE_COMPACT_AFTER_TURNS",
    )
    codex_native_read_turns: bool = Field(
        default=False,
        alias="TRADECRAFT_CODEX_NATIVE_READ_TURNS",
    )
    codex_native_account_check_interval_sec: int = Field(
        default=300,
        alias="TRADECRAFT_CODEX_NATIVE_ACCOUNT_CHECK_INTERVAL_SEC",
    )
    codex_native_model_check_interval_sec: int = Field(
        default=900,
        alias="TRADECRAFT_CODEX_NATIVE_MODEL_CHECK_INTERVAL_SEC",
    )
    codex_native_developer_instructions_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_CODEX_NATIVE_DEVELOPER_INSTRUCTIONS_ENABLED",
    )
```

- [ ] **Step 4: Extend `CodexNativeConfig`**

In `src/tradecraft/services/codex_native.py`, extend `CodexNativeConfig`:

```python
@dataclass(slots=True)
class CodexNativeConfig:
    mode: str = "sdk"
    sdk_codex_bin: str = ""
    timeout_ms: int = 60000
    model: str = DEFAULT_LLM_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    usage_enabled: bool = True
    usage_db_path: str = ".runtime/llm_usage.db"
    usage_component: str = "unknown"
    thread_mode: str = "daily"
    thread_db_path: str = ".runtime/codex_native_threads.db"
    compact_after_turns: int = 8
    read_turns: bool = False
    developer_instructions_enabled: bool = True
```

- [ ] **Step 5: Wire settings into runtime construction**

In `src/tradecraft/main.py`, every `CodexNativeConfig(...)` creation should include:

```python
        thread_mode=settings.codex_native_thread_mode,
        thread_db_path=settings.codex_native_thread_db_path,
        compact_after_turns=settings.codex_native_compact_after_turns,
        read_turns=settings.codex_native_read_turns,
        developer_instructions_enabled=settings.codex_native_developer_instructions_enabled,
```

Apply this to:

- `helper_codex_runtime`
- `daily_discovery_codex_runtime`
- `binance_manager_codex_runtime`
- `crypto_market_research_codex_runtime`
- any additional `CodexNativeRuntime(CodexNativeConfig(...))` in `src/tradecraft/main.py`.

- [ ] **Step 6: Expose settings metadata**

In `src/tradecraft/services/settings_catalog.py`, add `SettingMeta` entries:

```python
    "codex_native_thread_mode": SettingMeta(
        label="Codex Native Thread Mode",
        category="AI/LLM",
        description="Controls whether Codex SDK calls use fresh, daily, or persistent native threads.",
        input_type="select",
        options=["ephemeral", "daily", "persistent"],
        restart_required=True,
    ),
    "codex_native_compact_after_turns": SettingMeta(
        label="Codex Native Compact After Turns",
        category="AI/LLM",
        description="Compacts a native thread after this many recorded turns.",
        input_type="number",
        restart_required=True,
    ),
    "codex_native_read_turns": SettingMeta(
        label="Codex Native Read Turns",
        category="AI/LLM",
        description="Stores thread.read snapshots for native turn debugging when enabled.",
        input_type="toggle",
        restart_required=True,
    ),
```

If `SettingMeta` currently uses different field names, match the local dataclass exactly and keep the same content.

- [ ] **Step 7: Document env keys**

Add to `.env.example` near native settings:

```bash
TRADECRAFT_CODEX_NATIVE_THREAD_MODE=daily
TRADECRAFT_CODEX_NATIVE_THREAD_DB_PATH=.runtime/codex_native_threads.db
TRADECRAFT_CODEX_NATIVE_COMPACT_AFTER_TURNS=8
TRADECRAFT_CODEX_NATIVE_READ_TURNS=false
TRADECRAFT_CODEX_NATIVE_ACCOUNT_CHECK_INTERVAL_SEC=300
TRADECRAFT_CODEX_NATIVE_MODEL_CHECK_INTERVAL_SEC=900
TRADECRAFT_CODEX_NATIVE_DEVELOPER_INSTRUCTIONS_ENABLED=true
```

- [ ] **Step 8: Run config tests**

Run:

```bash
pytest tests/test_config.py::test_codex_native_thread_settings -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/tradecraft/config.py src/tradecraft/services/codex_native.py src/tradecraft/main.py src/tradecraft/services/settings_catalog.py .env.example tests/test_config.py
git commit -m "feat: configure codex native thread runtime"
```

---

## Task 3: Persistent Thread Start/Resume/Compact

**Files:**
- Modify: `src/tradecraft/services/codex_native.py`
- Test: `tests/test_codex_native.py`

- [ ] **Step 1: Extend fake SDK in tests**

Update `_install_fake_codex()` in `tests/test_codex_native.py` so fake `AsyncCodex` supports `thread_resume`, `thread.read`, and `thread.compact`:

```python
    class _Thread:
        def __init__(self, thread_id: str = "thread_new") -> None:
            self.id = thread_id

        async def run(self, prompt, **kwargs):
            seen["prompt"] = prompt
            seen["run_kwargs"] = kwargs
            if fail is not None:
                raise fail
            return _Result()

        async def read(self, include_turns: bool = False):
            seen["read_include_turns"] = include_turns
            return {"thread_id": self.id, "turns": [] if include_turns else None}

        async def compact(self):
            seen["compacted"] = True

    class _AsyncCodex:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs

        async def __aenter__(self):
            seen["entered"] = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            seen["closed"] = True

        async def thread_start(self, **kwargs):
            seen["thread_kwargs"] = kwargs
            return _Thread("thread_new")

        async def thread_resume(self, thread_id: str, **kwargs):
            seen["resume_thread_id"] = thread_id
            seen["resume_kwargs"] = kwargs
            return _Thread(thread_id)
```

- [ ] **Step 2: Write failing persistent thread test**

Add:

```python
def test_codex_runtime_reuses_daily_thread(monkeypatch, tmp_path: Path) -> None:
    captured = _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            usage_component="kis_block_manager",
            thread_mode="daily",
            thread_db_path=str(tmp_path / "native_threads.db"),
        )
    )

    payload = {
        "telemetry": {"component": "kis_block_manager", "operation": "manager_cycle"},
        "jue_workflow": {"workflow_id": "kis_intraday_manager"},
        "messages": [{"role": "user", "content": "Return JSON."}],
    }

    first = asyncio.run(runtime.complete(payload))
    assert first["ok"] is True
    assert captured["thread_kwargs"]["ephemeral"] is False
    assert "resume_thread_id" not in captured

    captured.clear()
    _install_fake_codex(monkeypatch, captured=captured)
    second = asyncio.run(runtime.complete(payload))
    assert second["ok"] is True
    assert captured["resume_thread_id"] == "thread_new"
```

- [ ] **Step 3: Write compact threshold test**

Add:

```python
def test_codex_runtime_compacts_after_threshold(monkeypatch, tmp_path: Path) -> None:
    captured = _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            usage_component="memory_reflection",
            thread_mode="daily",
            thread_db_path=str(tmp_path / "native_threads.db"),
            compact_after_turns=1,
        )
    )

    payload = {
        "telemetry": {"component": "memory_reflection", "operation": "block_reflection"},
        "jue_workflow": {"workflow_id": "block_reflection"},
        "messages": [{"role": "user", "content": "Return JSON."}],
    }

    asyncio.run(runtime.complete(payload))
    captured.clear()
    _install_fake_codex(monkeypatch, captured=captured)
    asyncio.run(runtime.complete(payload))
    assert captured["compacted"] is True
```

- [ ] **Step 4: Run tests and confirm they fail**

Run:

```bash
pytest tests/test_codex_native.py::test_codex_runtime_reuses_daily_thread tests/test_codex_native.py::test_codex_runtime_compacts_after_threshold -q
```

Expected: FAIL because runtime always starts ephemeral threads and does not compact.

- [ ] **Step 5: Implement thread keys and thread mode**

In `src/tradecraft/services/codex_native.py`, import:

```python
import hashlib
from tradecraft.services.codex_native_store import CodexNativeStore
```

Add helpers inside `CodexNativeRuntime`:

```python
    def _store(self) -> CodexNativeStore:
        return CodexNativeStore(self.config.thread_db_path)

    def _component_operation_workflow(self, payload: dict[str, Any]) -> tuple[str, str, str]:
        telemetry = payload.get("telemetry") if isinstance(payload.get("telemetry"), dict) else {}
        structured = self._structured_prompt_payload(payload)
        workflow = structured.get("jue_workflow") if isinstance(structured, dict) else {}
        workflow_id = str(workflow.get("workflow_id") or "").strip() if isinstance(workflow, dict) else ""
        component = str(telemetry.get("component") or self.config.usage_component or "unknown").strip()
        operation = str(telemetry.get("operation") or payload.get("operation") or "").strip()
        return component or "unknown", operation, workflow_id

    def _thread_key(self, payload: dict[str, Any]) -> tuple[str, bool]:
        mode = str(self.config.thread_mode or "daily").strip().lower()
        if mode in {"none", "off", "ephemeral"}:
            return "", True
        component, _operation, workflow_id = self._component_operation_workflow(payload)
        now = datetime.now(timezone.utc)
        if mode == "persistent":
            suffix = "persistent"
        else:
            suffix = now.strftime("%Y-%m-%d")
        key = ":".join(
            part for part in (component, workflow_id or "generic", suffix) if part
        )
        return key, False

    def _stable_hash(self, value: Any) -> str:
        return hashlib.sha256(safe_json_dumps(value).encode("utf-8")).hexdigest()
```

- [ ] **Step 6: Modify `_call_sdk()` to resume threads**

Inside `_call_sdk()`, before `run_turn()`, compute:

```python
        thread_key, ephemeral = self._thread_key(payload)
        component, operation, workflow_id = self._component_operation_workflow(payload)
        skill_refs = self._native_skill_refs(payload)
        input_hash = self._stable_hash(payload)
        output_schema_hash = self._stable_hash(output_schema or {})
```

Inside `run_turn()`, replace unconditional `thread_start` with:

```python
                thread_kwargs: dict[str, Any] = {
                    "model": self.resolved_model,
                    "cwd": str(Path.cwd()),
                    "ephemeral": ephemeral,
                }
                if sandbox is not None:
                    thread_kwargs["sandbox"] = sandbox
                if approval_mode is not None:
                    thread_kwargs["approval_mode"] = approval_mode

                store = self._store()
                thread = None
                stored_thread = None if ephemeral else store.get_active_thread(thread_key)
                if stored_thread is not None and hasattr(codex, "thread_resume"):
                    resume_kwargs = {
                        "model": self.resolved_model,
                    }
                    if sandbox is not None:
                        resume_kwargs["sandbox"] = sandbox
                    if approval_mode is not None:
                        resume_kwargs["approval_mode"] = approval_mode
                    thread = await codex.thread_resume(
                        stored_thread["thread_id"],
                        **resume_kwargs,
                    )
                if thread is None:
                    thread = await codex.thread_start(**thread_kwargs)

                thread_id = str(getattr(thread, "id", "") or stored_thread.get("thread_id") if stored_thread else "")
                if not thread_id:
                    thread_id = str(getattr(thread, "thread_id", "") or "unknown")
                if not ephemeral:
                    store.upsert_thread(
                        thread_key=thread_key,
                        thread_id=thread_id,
                        component=component,
                        workflow_id=workflow_id,
                        model=self.resolved_model,
                        reasoning_effort=self.resolved_reasoning_effort,
                        status="active",
                        metadata={
                            "operation": operation,
                            "skill_refs": skill_refs,
                            "thread_mode": self.config.thread_mode,
                        },
                    )

                if (
                    not ephemeral
                    and self.config.compact_after_turns > 0
                    and store.count_turns_for_thread(thread_key) >= self.config.compact_after_turns
                    and hasattr(thread, "compact")
                ):
                    await thread.compact()
                    store.mark_thread_compacted(thread_key)
```

Keep `run_kwargs` unchanged, then return a dict with thread metadata:

```python
                result = await thread.run(run_input, **run_kwargs)
                thread_read = None
                if self.config.read_turns and hasattr(thread, "read"):
                    thread_read = await thread.read(include_turns=True)
                return {
                    "result": result,
                    "thread_id": thread_id,
                    "thread_key": thread_key,
                    "component": component,
                    "operation": operation,
                    "workflow_id": workflow_id,
                    "skill_refs": skill_refs,
                    "input_hash": input_hash,
                    "output_schema_hash": output_schema_hash,
                    "thread_read": thread_read,
                }
```

After `asyncio.wait_for`, adapt:

```python
        run_payload = await asyncio.wait_for(run_turn(), timeout=timeout_sec)
        result = run_payload["result"]
        content = str(getattr(result, "final_response", "") or "")
        usage = self._sdk_usage(getattr(result, "usage", None))
        return json.dumps(
            {
                "content": content,
                "raw": content,
                "usage": usage,
                "native": {
                    "thread_id": run_payload["thread_id"],
                    "thread_key": run_payload["thread_key"],
                    "component": run_payload["component"],
                    "operation": run_payload["operation"],
                    "workflow_id": run_payload["workflow_id"],
                    "skill_refs": run_payload["skill_refs"],
                    "input_hash": run_payload["input_hash"],
                    "output_schema_hash": run_payload["output_schema_hash"],
                    "thread_read": run_payload["thread_read"],
                },
            },
            ensure_ascii=False,
        )
```

- [ ] **Step 7: Record native turn metadata**

In `_record_usage_sync()`, after `repo.record_call(...)`, add:

```python
        native = (result or {}).get("native") if isinstance((result or {}).get("native"), dict) else {}
        thread_key = str(native.get("thread_key") or "")
        thread_id = str(native.get("thread_id") or "")
        if thread_key and thread_id:
            CodexNativeStore(self.config.thread_db_path).record_turn(
                thread_key=thread_key,
                thread_id=thread_id,
                component=component,
                operation=operation,
                workflow_id=str(native.get("workflow_id") or ""),
                model=self.resolved_model,
                reasoning_effort=self.resolved_reasoning_effort,
                status=status,
                latency_ms=int((finished_at - started_at).total_seconds() * 1000),
                input_hash=str(native.get("input_hash") or ""),
                output_schema_hash=str(native.get("output_schema_hash") or ""),
                skill_refs=list(native.get("skill_refs") or []),
                usage=usage,
                error_message=error_message,
                result={"content": output_text[:4000]},
                thread_read=native.get("thread_read") if isinstance(native.get("thread_read"), dict) else None,
            )
```

If `CodexNativeStore` import creates a circular import, move store import to function scope inside `_record_usage_sync()`.

- [ ] **Step 8: Run thread tests**

Run:

```bash
pytest tests/test_codex_native.py::test_codex_runtime_reuses_daily_thread tests/test_codex_native.py::test_codex_runtime_compacts_after_threshold -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/tradecraft/services/codex_native.py tests/test_codex_native.py
git commit -m "feat: persist codex native threads"
```

---

## Task 4: Native Instruction Hierarchy

**Files:**
- Create: `src/tradecraft/services/codex_instructions.py`
- Modify: `src/tradecraft/services/codex_native.py`
- Test: `tests/test_codex_native.py`

- [ ] **Step 1: Write instruction builder tests**

Add:

```python
def test_codex_instruction_pack_uses_developer_instructions() -> None:
    from tradecraft.services.codex_instructions import build_codex_instruction_pack

    payload = {
        "telemetry": {"component": "kis_block_manager"},
        "jue_workflow": {
            "workflow_id": "kis_intraday_manager",
            "scope": "KRX block trading",
            "language_policy": {"internal_reasoning_language": "English", "user_visible_language": "Korean"},
            "authority": {"llm": "block manager", "executor": "rule engine"},
            "safety_gates": ["cash_check", "kill_switch", "duplicate_order_guard"],
        },
    }

    pack = build_codex_instruction_pack(
        payload,
        component="kis_block_manager",
        model="gpt-5.5",
        reasoning_effort="xhigh",
    )

    assert "HERMES/Jue" in pack["base_instructions"]
    assert "Think in English" in pack["developer_instructions"]
    assert "Respond to the user in Korean" in pack["developer_instructions"]
    assert "cash_check" in pack["developer_instructions"]
    assert "duplicate_order_guard" in pack["developer_instructions"]
```

- [ ] **Step 2: Run test and confirm missing module**

Run:

```bash
pytest tests/test_codex_native.py::test_codex_instruction_pack_uses_developer_instructions -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement instruction pack**

Create `src/tradecraft/services/codex_instructions.py`:

```python
from __future__ import annotations

from typing import Any


def _workflow(payload: dict[str, Any]) -> dict[str, Any]:
    workflow = payload.get("jue_workflow")
    return workflow if isinstance(workflow, dict) else {}


def build_codex_instruction_pack(
    payload: dict[str, Any],
    *,
    component: str,
    model: str,
    reasoning_effort: str,
) -> dict[str, str]:
    workflow = _workflow(payload)
    workflow_id = str(workflow.get("workflow_id") or "generic")
    scope = str(workflow.get("scope") or component or "HERMES runtime")
    language_policy = workflow.get("language_policy") if isinstance(workflow.get("language_policy"), dict) else {}
    authority = workflow.get("authority") if isinstance(workflow.get("authority"), dict) else {}
    safety_gates = workflow.get("safety_gates") if isinstance(workflow.get("safety_gates"), list) else []

    base_instructions = (
        "You are HERMES/Jue running inside the Codex native runtime. "
        "You are an active trading partner for block-based trading research, judgment, and reflection. "
        "You never bypass HERMES safety gates or adapters; you return structured decisions only."
    )

    developer_lines = [
        f"Workflow: {workflow_id}",
        f"Scope: {scope}",
        f"Runtime model: {model}",
        f"Reasoning effort: {reasoning_effort}",
        "Think in English for analysis and structure.",
        "Respond to the user-visible surface in Korean when a user-visible conclusion is required.",
        "Separate evidence, thesis, risk, execution price structure, and next action.",
        "Do not invent account balances, fills, prices, research citations, or block state.",
        "If required evidence is absent, mark the gap explicitly and keep the action executable.",
    ]
    if language_policy:
        developer_lines.append(f"Language policy: {language_policy}")
    if authority:
        developer_lines.append(f"Authority boundaries: {authority}")
    if safety_gates:
        developer_lines.append("Safety gates that always outrank strategy: " + ", ".join(str(item) for item in safety_gates))

    return {
        "base_instructions": base_instructions,
        "developer_instructions": "\n".join(developer_lines),
    }
```

- [ ] **Step 4: Pass instructions into SDK**

In `src/tradecraft/services/codex_native.py`, import:

```python
from tradecraft.services.codex_instructions import build_codex_instruction_pack
```

In `_call_sdk()`, after `component, operation, workflow_id = ...`, add:

```python
        instruction_pack = build_codex_instruction_pack(
            self._structured_prompt_payload(payload),
            component=component,
            model=self.resolved_model,
            reasoning_effort=self.resolved_reasoning_effort,
        )
```

In `thread_kwargs`, when enabled:

```python
                if self.config.developer_instructions_enabled:
                    thread_kwargs["base_instructions"] = instruction_pack["base_instructions"]
                    thread_kwargs["developer_instructions"] = instruction_pack["developer_instructions"]
```

In `resume_kwargs`, when enabled:

```python
                    if self.config.developer_instructions_enabled:
                        resume_kwargs["developer_instructions"] = instruction_pack["developer_instructions"]
```

- [ ] **Step 5: Add runtime test**

Add:

```python
def test_codex_runtime_passes_developer_instructions(monkeypatch, tmp_path: Path) -> None:
    captured = _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            thread_mode="daily",
            thread_db_path=str(tmp_path / "native_threads.db"),
        )
    )

    asyncio.run(
        runtime.complete(
            {
                "telemetry": {"component": "kis_block_manager"},
                "jue_workflow": {
                    "workflow_id": "kis_intraday_manager",
                    "scope": "KRX block trading",
                    "safety_gates": ["cash_check"],
                },
                "messages": [{"role": "user", "content": "Return JSON."}],
            }
        )
    )

    assert "base_instructions" in captured["thread_kwargs"]
    assert "developer_instructions" in captured["thread_kwargs"]
    assert "cash_check" in captured["thread_kwargs"]["developer_instructions"]
```

- [ ] **Step 6: Run instruction tests**

Run:

```bash
pytest tests/test_codex_native.py::test_codex_instruction_pack_uses_developer_instructions tests/test_codex_native.py::test_codex_runtime_passes_developer_instructions -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/tradecraft/services/codex_instructions.py src/tradecraft/services/codex_native.py tests/test_codex_native.py
git commit -m "feat: add codex native instruction hierarchy"
```

---

## Task 5: Contract-Backed Native Output Schemas

**Files:**
- Create: `src/tradecraft/services/codex_contracts.py`
- Modify: `src/tradecraft/services/codex_native.py`
- Test: `tests/test_codex_native.py`
- Test: `tests/test_jue_workflow_manifests.py`

- [ ] **Step 1: Write contract schema test**

Add to `tests/test_codex_native.py`:

```python
def test_codex_contract_schema_loads_block_action_contract() -> None:
    from tradecraft.services.codex_contracts import CodexContractSchemaLoader

    loader = CodexContractSchemaLoader()
    schema = loader.schema_for_contract_ids(["block_action_contract"])

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "contract_id" in schema["properties"]
    assert schema["properties"]["contract_id"]["enum"] == ["block_action_contract"]
    assert "decision" in schema["properties"]
```

- [ ] **Step 2: Write runtime contract preference test**

Add:

```python
def test_codex_runtime_prefers_workflow_contract_schema(monkeypatch) -> None:
    captured = _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(CodexNativeConfig(usage_enabled=False))

    asyncio.run(
        runtime.complete(
            {
                "telemetry": {"component": "kis_block_manager"},
                "jue_workflow": {
                    "workflow_id": "kis_intraday_manager",
                    "contracts": [{"contract_id": "block_action_contract"}],
                },
                "output_schema": {"legacy": "string"},
                "messages": [{"role": "user", "content": "Return JSON."}],
            }
        )
    )

    schema = captured["run_kwargs"]["output_schema"]
    assert schema["properties"]["contract_id"]["enum"] == ["block_action_contract"]
    assert "legacy" not in schema["properties"]
```

- [ ] **Step 3: Run tests and confirm missing loader**

Run:

```bash
pytest tests/test_codex_native.py::test_codex_contract_schema_loads_block_action_contract tests/test_codex_native.py::test_codex_runtime_prefers_workflow_contract_schema -q
```

Expected: FAIL with missing module or legacy schema still used.

- [ ] **Step 4: Implement contract loader**

Create `src/tradecraft/services/codex_contracts.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tradecraft.services.jue_skill_registry import JueSkillRegistry


def _string_array() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


@dataclass(frozen=True)
class CodexContractSchemaLoader:
    registry: JueSkillRegistry = field(default_factory=JueSkillRegistry)

    def schema_for_contract_ids(self, contract_ids: list[str]) -> dict[str, Any] | None:
        ids = [str(value or "").strip() for value in contract_ids if str(value or "").strip()]
        if not ids:
            return None
        if len(ids) == 1:
            return self._schema_for_contract(ids[0])
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selected_contract_id": {"type": "string", "enum": ids},
                "payload": {
                    "oneOf": [self._schema_for_contract(contract_id) for contract_id in ids],
                },
            },
            "required": ["selected_contract_id", "payload"],
        }

    def _schema_for_contract(self, contract_id: str) -> dict[str, Any]:
        contract = self.registry.load_contract(contract_id)
        required_names = [str(value) for value in contract.get("required") or []]
        properties: dict[str, Any] = {
            "contract_id": {"type": "string", "enum": [contract_id]},
            "version": {"type": "integer"},
            "decision": {"type": "string"},
            "reasons": _string_array(),
            "risks": _string_array(),
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "source": {"type": "string"},
                        "claim": {"type": "string"},
                        "as_of": {"type": "string"},
                    },
                    "required": ["source", "claim", "as_of"],
                },
            },
            "data_gaps": _string_array(),
            "next_actions": _string_array(),
        }
        for name in required_names:
            properties.setdefault(name, {"type": "string"})
        required = ["contract_id", "version", *required_names]
        deduped_required = list(dict.fromkeys(required))
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": deduped_required,
        }
```

- [ ] **Step 5: Use contracts inside native runtime**

In `src/tradecraft/services/codex_native.py`, import:

```python
from tradecraft.services.codex_contracts import CodexContractSchemaLoader
```

Add helper:

```python
    def _contract_ids_from_payload(self, payload: dict[str, Any]) -> list[str]:
        structured = self._structured_prompt_payload(payload)
        workflow = structured.get("jue_workflow") if isinstance(structured, dict) else {}
        contracts = workflow.get("contracts") if isinstance(workflow, dict) else None
        ids: list[str] = []
        if isinstance(contracts, list):
            for row in contracts:
                if isinstance(row, dict):
                    contract_id = str(row.get("contract_id") or "").strip()
                    if contract_id:
                        ids.append(contract_id)
        explicit = structured.get("contract_id") if isinstance(structured, dict) else ""
        if isinstance(explicit, str) and explicit.strip():
            ids.append(explicit.strip())
        return list(dict.fromkeys(ids))
```

At the top of `_native_output_schema()`, before `native_output_schema` and `output_schema` handling:

```python
        contract_ids = self._contract_ids_from_payload(payload)
        if contract_ids:
            contract_schema = CodexContractSchemaLoader().schema_for_contract_ids(contract_ids)
            if contract_schema:
                return contract_schema
```

- [ ] **Step 6: Run contract tests**

Run:

```bash
pytest tests/test_codex_native.py::test_codex_contract_schema_loads_block_action_contract tests/test_codex_native.py::test_codex_runtime_prefers_workflow_contract_schema tests/test_jue_workflow_manifests.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/tradecraft/services/codex_contracts.py src/tradecraft/services/codex_native.py tests/test_codex_native.py tests/test_jue_workflow_manifests.py
git commit -m "feat: derive codex output schemas from jue contracts"
```

---

## Task 6: SDK Account/Models Readiness

**Files:**
- Modify: `src/tradecraft/services/codex_native.py`
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_codex_native.py`
- Test: `tests/test_api_smoke.py`

- [ ] **Step 1: Write runtime readiness tests**

Add:

```python
def test_codex_runtime_account_and_models(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class _AsyncCodex:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def account(self, refresh_token: bool = False):
            seen["refresh_token"] = refresh_token
            return {"email": "user@example.com", "plan": "codex"}

        async def models(self, include_hidden: bool = False):
            seen["include_hidden"] = include_hidden
            return [{"id": "gpt-5.5"}, {"id": "gpt-5.3-codex-spark"}]

    monkeypatch.setattr(
        "tradecraft.services.codex_native._import_openai_codex",
        lambda: types.SimpleNamespace(AsyncCodex=_AsyncCodex),
    )

    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            thread_db_path=str(tmp_path / "native_threads.db"),
        )
    )

    account = asyncio.run(runtime.check_account())
    models = asyncio.run(runtime.list_models())

    assert account["status"] == "ok"
    assert account["account"]["email"] == "u***@example.com"
    assert models["status"] == "ok"
    assert models["models"] == ["gpt-5.3-codex-spark", "gpt-5.5"]
```

- [ ] **Step 2: Run runtime readiness test and confirm missing methods**

Run:

```bash
pytest tests/test_codex_native.py::test_codex_runtime_account_and_models -q
```

Expected: FAIL because `check_account()` and `list_models()` do not exist.

- [ ] **Step 3: Implement readiness helpers**

In `src/tradecraft/services/codex_native.py`, add:

```python
def _redact_email(value: str) -> str:
    text = str(value or "").strip()
    if "@" not in text:
        return text[:2] + "***" if text else ""
    name, domain = text.split("@", 1)
    return f"{name[:1]}***@{domain}"
```

Inside `CodexNativeRuntime`, add:

```python
    async def check_account(self) -> dict[str, Any]:
        module = _import_openai_codex()
        if module is None:
            return {"status": "error", "error_message": "openai-codex Python SDK is not installed"}
        async_codex = getattr(module, "AsyncCodex", None)
        if async_codex is None:
            return {"status": "error", "error_message": "openai-codex SDK does not expose AsyncCodex"}
        try:
            async with async_codex(**self._client_kwargs(module)) as codex:
                account_fn = getattr(codex, "account", None)
                if account_fn is None:
                    return {"status": "error", "error_message": "Codex SDK account() is unavailable"}
                raw = await account_fn(refresh_token=False)
        except Exception as exc:
            CodexNativeStore(self.config.thread_db_path).record_account_check(
                status="error",
                account_label="",
                detail={},
                error_message=str(exc),
            )
            return {"status": "error", "error_message": str(exc)}
        account = raw if isinstance(raw, dict) else {"raw": str(raw)}
        if isinstance(account.get("email"), str):
            account["email"] = _redact_email(account["email"])
        CodexNativeStore(self.config.thread_db_path).record_account_check(
            status="ok",
            account_label=str(account.get("email") or account.get("id") or ""),
            detail=account,
            error_message="",
        )
        return {"status": "ok", "account": account}

    async def list_models(self) -> dict[str, Any]:
        module = _import_openai_codex()
        if module is None:
            return {"status": "error", "models": [], "error_message": "openai-codex Python SDK is not installed"}
        async_codex = getattr(module, "AsyncCodex", None)
        if async_codex is None:
            return {"status": "error", "models": [], "error_message": "openai-codex SDK does not expose AsyncCodex"}
        try:
            async with async_codex(**self._client_kwargs(module)) as codex:
                models_fn = getattr(codex, "models", None)
                if models_fn is None:
                    return {"status": "error", "models": [], "error_message": "Codex SDK models() is unavailable"}
                raw = await models_fn(include_hidden=False)
        except Exception as exc:
            return {"status": "error", "models": [], "error_message": str(exc)}
        models: list[str] = []
        for row in raw if isinstance(raw, list) else []:
            if isinstance(row, dict):
                model = str(row.get("id") or row.get("name") or "").strip()
            else:
                model = str(getattr(row, "id", "") or getattr(row, "name", "") or "").strip()
            if model:
                models.append(model)
                CodexNativeStore(self.config.thread_db_path).record_model_check(
                    model=model,
                    available=True,
                    detail=row if isinstance(row, dict) else {"raw": str(row)},
                    error_message="",
                )
        return {"status": "ok", "models": sorted(set(models))}

    def _client_kwargs(self, module: Any) -> dict[str, Any]:
        codex_config = getattr(module, "CodexConfig", None)
        sdk_codex_bin = self._sdk_codex_bin()
        if sdk_codex_bin and codex_config is not None:
            return {"config": codex_config(codex_bin=sdk_codex_bin)}
        return {}
```

Then update `_call_sdk()` to use `self._client_kwargs(module)` instead of duplicate client config creation.

- [ ] **Step 4: Add API test**

Add to `tests/test_api_smoke.py`:

```python
def test_ops_readiness_includes_codex_native_status(monkeypatch, tmp_path) -> None:
    headers = _admin_headers(monkeypatch)
    monkeypatch.setattr(settings, "codex_native_thread_db_path", str(tmp_path / "native_threads.db"))

    with TestClient(app) as client:
        response = client.get("/api/ops/readiness", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert "codex_native" in payload
    assert payload["codex_native"]["mode"] in {"sdk", "none"}
    assert "thread_mode" in payload["codex_native"]
    assert "models" in payload["codex_native"]
```

- [ ] **Step 5: Extend readiness payload**

In `src/tradecraft/main.py`, locate `_build_ops_readiness()` and add:

```python
    native_store = CodexNativeStore(settings.codex_native_thread_db_path)
    codex_native_status = {
        "mode": helper_codex_runtime.mode,
        "model": helper_codex_runtime.resolved_model,
        "reasoning_effort": helper_codex_runtime.resolved_reasoning_effort,
        "thread_mode": settings.codex_native_thread_mode,
        "thread_db_path": settings.codex_native_thread_db_path,
        "latest_account_check": native_store.latest_account_check(),
        "models": native_store.list_model_checks(),
        "recent_turns": native_store.list_recent_turns(limit=8),
    }
```

Return it under:

```python
        "codex_native": codex_native_status,
```

Also add:

```python
@app.get("/api/codex/native/status")
async def codex_native_status(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    store = CodexNativeStore(settings.codex_native_thread_db_path)
    return {
        "status": "ok",
        "mode": helper_codex_runtime.mode,
        "thread_mode": settings.codex_native_thread_mode,
        "account": store.latest_account_check(),
        "models": store.list_model_checks(),
        "recent_turns": store.list_recent_turns(limit=20),
    }
```

Import `CodexNativeStore` at the top of `src/tradecraft/main.py`.

- [ ] **Step 6: Run readiness tests**

Run:

```bash
pytest tests/test_codex_native.py::test_codex_runtime_account_and_models tests/test_api_smoke.py::test_ops_readiness_includes_codex_native_status -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/tradecraft/services/codex_native.py src/tradecraft/main.py tests/test_codex_native.py tests/test_api_smoke.py
git commit -m "feat: expose codex native account and model readiness"
```

---

## Task 7: UI Native Status Panel

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Write static UI test**

Add to `tests/test_static_ui.py`:

```python
def test_settings_page_exposes_codex_native_status() -> None:
    js = _js()

    assert "function renderCodexNativeStatus" in js
    assert '"/codex/native/status"' in js
    assert "data-codex-native-panel" in js
    assert "Codex Native" in js
```

- [ ] **Step 2: Run static UI test and confirm missing function**

Run:

```bash
pytest tests/test_static_ui.py::test_settings_page_exposes_codex_native_status -q
```

Expected: FAIL.

- [ ] **Step 3: Add UI fetch and renderer**

In `src/tradecraft/web/static/app.js`, near existing settings/Jue workflow renderers, add:

```javascript
function renderCodexNativeStatus(payload) {
  const account = payload?.account || payload?.latest_account_check || {};
  const models = Array.isArray(payload?.models) ? payload.models : [];
  const recentTurns = Array.isArray(payload?.recent_turns) ? payload.recent_turns : [];
  const accountStatus = account?.status || "unknown";
  const modelItems = models.length
    ? models.map((row) => {
        const model = row.model || row;
        const ok = row.available === undefined ? true : Boolean(row.available);
        return `<span class="strategy-data-chip ${ok ? "positive" : "warn"}">${escapeHTML(model)}</span>`;
      }).join("")
    : `<span class="strategy-data-chip muted">model check pending</span>`;
  const turnRows = recentTurns.slice(0, 6).map((row) => `
    <div class="ops-mini-row">
      <span>${escapeHTML(row.component || "-")}</span>
      <span>${escapeHTML(row.workflow_id || row.operation || "-")}</span>
      <span class="${row.status === "ok" ? "positive" : "negative"}">${escapeHTML(row.status || "-")}</span>
      <span class="mono">${escapeHTML(row.model || "-")}</span>
    </div>
  `).join("");

  return `
    <section class="settings-card" data-codex-native-panel>
      <div class="settings-card-header">
        <div>
          <h3>Codex Native</h3>
          <p>Jue 판단 런타임의 계정, 모델, thread, 최근 turn 상태입니다.</p>
        </div>
        <span class="pill mono">${escapeHTML(payload?.mode || "sdk")} · ${escapeHTML(payload?.thread_mode || "-")}</span>
      </div>
      <div class="settings-status-grid">
        <div>
          <span class="muted">Account</span>
          <strong>${escapeHTML(accountStatus)}</strong>
        </div>
        <div>
          <span class="muted">Thread DB</span>
          <strong class="mono">${escapeHTML(payload?.thread_db_path || "-")}</strong>
        </div>
      </div>
      <div class="chip-row">${modelItems}</div>
      <div class="ops-mini-table">${turnRows || `<div class="muted">최근 native turn 없음</div>`}</div>
    </section>
  `;
}
```

In the settings page loading flow, fetch:

```javascript
const codexNativeStatus = await getJSON("/codex/native/status");
```

Then insert:

```javascript
${renderCodexNativeStatus(codexNativeStatus)}
```

near the existing Jue workflow status panel.

- [ ] **Step 4: Add minimal CSS if needed**

In `src/tradecraft/web/static/style.css`, add only if these classes are absent:

```css
.ops-mini-table {
  display: grid;
  gap: 6px;
}

.ops-mini-row {
  display: grid;
  grid-template-columns: minmax(110px, 1fr) minmax(120px, 1fr) 80px minmax(120px, 1fr);
  gap: 8px;
  align-items: center;
  padding: 7px 0;
  border-top: 1px solid var(--border-subtle);
  font-size: 12px;
}

@media (max-width: 720px) {
  .ops-mini-row {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 5: Run UI checks**

Run:

```bash
pytest tests/test_static_ui.py::test_settings_page_exposes_codex_native_status -q
node --check src/tradecraft/web/static/app.js
```

Expected: PASS and no JS syntax errors.

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css tests/test_static_ui.py
git commit -m "feat: show codex native runtime status"
```

---

## Task 8: Native Probe Uses Account/Model Checks

**Files:**
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_api_smoke.py`

- [ ] **Step 1: Extend probe test**

Modify `tests/test_api_smoke.py::test_llm_probe_endpoint_runs_small_bridge_call` or add:

```python
def test_llm_probe_returns_native_runtime_metadata(monkeypatch) -> None:
    headers = _admin_headers(monkeypatch)

    with TestClient(app) as client:
        response = client.post("/api/llm/probe", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert "native_runtime" in payload
    assert payload["native_runtime"] is True
    assert "thread_mode" in payload
```

- [ ] **Step 2: Run test and confirm missing fields**

Run:

```bash
pytest tests/test_api_smoke.py::test_llm_probe_returns_native_runtime_metadata -q
```

Expected: FAIL because probe does not yet include these fields.

- [ ] **Step 3: Update `/api/llm/probe` response**

In `src/tradecraft/main.py`, update response:

```python
    return {
        "status": "ok" if ok else "error",
        "ok": ok,
        "native_runtime": True,
        "mode": str(result.get("mode") or helper_codex_runtime.mode),
        "model": helper_codex_runtime.resolved_model,
        "reasoning_effort": helper_codex_runtime.resolved_reasoning_effort,
        "thread_mode": settings.codex_native_thread_mode,
        "latency_ms": latency_ms,
        "timeout_ms": timeout_ms,
        "content": str(result.get("content") or "")[:500],
        "error_message": "" if ok else str(result.get("error") or "")[:500],
    }
```

- [ ] **Step 4: Run probe test**

Run:

```bash
pytest tests/test_api_smoke.py::test_llm_probe_returns_native_runtime_metadata -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/main.py tests/test_api_smoke.py
git commit -m "feat: report native runtime metadata in llm probe"
```

---

## Task 9: End-To-End Regression And Real SDK Smoke

**Files:**
- No source files unless regressions are found.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
pytest tests/test_codex_native.py tests/test_config.py tests/test_api_smoke.py tests/test_jue_skill_registry.py tests/test_jue_workflow_manifests.py -q
```

Expected: PASS.

- [ ] **Step 2: Run trading prompt regressions**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_kis_manager_prompt_contains_jue_workflow_pack tests/test_binance_block_trader.py::test_binance_manager_prompt_contains_jue_workflow_pack tests/test_binance_block_trader.py::test_manager_accepts_complete_json_actions_and_creates_paper_blocks -q
```

Expected: PASS.

- [ ] **Step 3: Run static frontend checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
git diff --check
```

Expected: no output from `git diff --check`, JS check exits 0.

- [ ] **Step 4: Run real SDK smoke with admin-protected API**

Start or restart local control if needed, then run:

```bash
curl -sS -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" \
  -X POST http://127.0.0.1:18080/api/llm/probe | python3 -m json.tool
```

Expected:

```json
{
  "status": "ok",
  "ok": true,
  "native_runtime": true,
  "mode": "sdk"
}
```

The actual response includes more fields; the important parts are `status=ok`, `native_runtime=true`, and the expected model.

- [ ] **Step 5: Check native status endpoint**

Run:

```bash
curl -sS -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" \
  http://127.0.0.1:18080/api/codex/native/status | python3 -m json.tool
```

Expected:

- `mode` is `sdk`.
- `thread_mode` is `daily`, `persistent`, or configured value.
- `recent_turns` contains at least the probe turn after the previous step.

- [ ] **Step 6: Confirm old bridge remains removed**

Run a legacy bridge keyword scan across source, tests, examples, and active
documentation.

Expected: no active references. Runtime DB/history references under `.runtime`
may exist and are not part of source validation.

- [ ] **Step 7: Final commit**

```bash
git add src/tradecraft tests .env.example docs/superpowers/plans/2026-06-03-codex-native-absorption-v2.md
git commit -m "feat: deepen codex native runtime integration"
```

---

## Acceptance Criteria

- KIS Jue, Binance Jue, memory/reflection, research, and helper paths still call `CodexNativeRuntime`.
- No source code imports or references the old bridge module.
- Trading calls still use `Sandbox.read_only` and `ApprovalMode.deny_all`.
- Same daily/component workflow resumes an existing native thread when thread mode is `daily`.
- Thread mode `ephemeral` still starts one-off threads for operators who want fully stateless calls.
- `thread.compact()` is attempted after `TRADECRAFT_CODEX_NATIVE_COMPACT_AFTER_TURNS` recorded turns.
- `developer_instructions` carries Jue identity, language policy, workflow authority, and safety gates.
- Contract ids from `jue_workflow.contracts` produce native `output_schema`.
- `/api/ops/readiness` includes `codex_native`.
- `/api/codex/native/status` shows account/model check history and recent turns.
- Settings UI shows a Codex Native panel.
- Recent native turns are stored in `.runtime/codex_native_threads.db`.

## Risk Controls

- **Risk: persistent thread context becomes stale.** Use `daily` thread mode by default, compact after 8 turns, and allow `ephemeral` fallback.
- **Risk: token usage grows from thread context.** Store turn counts, compact frequently, and keep evidence payloads compact.
- **Risk: SDK account/model calls add latency.** Readiness endpoints use stored check results; live checks are explicit and cacheable.
- **Risk: schema is too generic for complex actions.** Contract-backed schema starts conservative; action-specific payload validation still remains in KIS/Binance services.
- **Risk: Codex SDK beta API changes.** Use `getattr()` feature detection for `account`, `models`, `thread_resume`, `read`, and `compact`.
- **Risk: native runtime accidentally gains trading authority.** Keep Codex SDK sandbox read-only and approval deny-all. Orders stay in existing adapters and rule executors.

## Self-Review

- **Spec coverage:** All five requested top-priority absorptions have task coverage: persistent threads in Tasks 1-3, instruction hierarchy in Task 4, contract schemas in Task 5, readiness in Tasks 6-8, turn metadata in Tasks 1 and 3.
- **No old bridge regression:** Task 9 explicitly scans for old bridge terms.
- **Type consistency:** `CodexNativeConfig` fields match config/env names and UI/readiness payload names.
- **Test discipline:** Every implementation task starts with a failing test and has a focused verification command.
- **Trading safety:** The plan does not move order execution into Codex native. It strengthens judgment/runtime observability only.
