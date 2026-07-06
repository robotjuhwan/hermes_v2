# LLM Usage Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add daily token/call statistics for every HERMES gpt-5.5 usage path: report knowledge extraction, research Q&A, strategy intelligence, market judge, investment memory, and 쥬 block manager.

**Architecture:** Record usage at the central `CodexNativeRuntime.complete()` boundary so every caller is covered without duplicating accounting logic. Each call writes one row to `.runtime/llm_usage.db`, then API/UI/Telegram read daily rollups by component, model, status, and exact-vs-estimated token source.

**Tech Stack:** Python 3.10, SQLite, FastAPI, static JS/CSS UI, pytest, existing `CodexNativeRuntime`.

---

## File Structure

- Create `src/tradecraft/services/llm_usage.py`
  - Owns SQLite schema, token estimation fallback, per-call recording, daily summaries.
- Modify `src/tradecraft/services/codex_native.py`
  - Adds optional telemetry config and records every `complete()` call.
- Modify `src/tradecraft/config.py`
  - Adds env settings for telemetry enablement and DB path.
- Modify `src/tradecraft/main.py`
  - Adds protected `/api/llm/usage/*` endpoints and wires main-app bridges with component names.
- Modify runner files:
  - `src/tradecraft/runtime/kis_block_trader_runner.py`
  - `src/tradecraft/runtime/investment_memory_runner.py`
  - `src/tradecraft/runtime/market_judge_runner.py`
  - `src/tradecraft/runtime/research_runner.py`
  - `src/tradecraft/runtime/strategy_insights_runner.py`
  - Purpose: pass component labels into each bridge.
- Modify report/research services that instantiate their own bridges:
  - `src/tradecraft/services/naver_reports.py`
  - `src/tradecraft/services/research_pipeline.py`
  - `src/tradecraft/services/portfolio_coach.py`
- Modify `src/tradecraft/services/telegram_cli.py`
  - Adds `/llm-usage` text rendering.
- Modify frontend:
  - `src/tradecraft/web/static/app.js`
  - `src/tradecraft/web/static/style.css`
  - Adds “LLM 사용량” panel in System/운영 area and a compact card in 블록 트레이딩/메모리 readiness.
- Tests:
  - Create `tests/test_llm_usage.py`
  - Modify `tests/test_codex_native.py`
  - Modify `tests/test_api_smoke.py`
  - Modify `tests/test_telegram_cli.py`

---

## Data Model

Create `.runtime/llm_usage.db`.

`llm_calls`:
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `started_at TEXT NOT NULL`
- `finished_at TEXT NOT NULL`
- `trading_day TEXT NOT NULL`
- `component TEXT NOT NULL`
- `operation TEXT NOT NULL DEFAULT ''`
- `model TEXT NOT NULL`
- `mode TEXT NOT NULL`
- `status TEXT NOT NULL`
- `latency_ms INTEGER NOT NULL DEFAULT 0`
- `prompt_tokens INTEGER NOT NULL DEFAULT 0`
- `completion_tokens INTEGER NOT NULL DEFAULT 0`
- `total_tokens INTEGER NOT NULL DEFAULT 0`
- `usage_source TEXT NOT NULL DEFAULT 'missing'`
- `input_chars INTEGER NOT NULL DEFAULT 0`
- `output_chars INTEGER NOT NULL DEFAULT 0`
- `error_message TEXT NOT NULL DEFAULT ''`
- `metadata_json TEXT NOT NULL DEFAULT '{}'`

Indexes:
- `idx_llm_calls_day_component` on `(trading_day, component, started_at DESC)`
- `idx_llm_calls_model` on `(model, trading_day)`
- `idx_llm_calls_status` on `(status, trading_day)`

Component names:
- `research_reports`
- `research_pipeline`
- `research_ask`
- `strategy_intelligence`
- `market_judge`
- `kis_block_manager`
- `investment_memory`
- `portfolio_coach`
- `unknown`

Token source:
- `exact`: provider/bridge returned real usage.
- `estimated`: no usage was returned, so we estimate from text length.
- `missing`: failed before input/output could be measured.

---

## Task 1: LLM Usage Repository

**Files:**
- Create: `src/tradecraft/services/llm_usage.py`
- Test: `tests/test_llm_usage.py`

- [ ] **Step 1: Write failing repository tests**

Add:

```python
from __future__ import annotations

from tradecraft.services.llm_usage import LLMUsageRepository, estimate_tokens


def test_estimate_tokens_is_stable_for_korean_and_json_text() -> None:
    assert estimate_tokens("삼성전자 목표가와 손절가를 검토한다.") >= 6
    assert estimate_tokens('{"symbol":"005930","qty":2}') >= 6


def test_record_call_and_daily_summary(tmp_path) -> None:
    repo = LLMUsageRepository(str(tmp_path / "llm_usage.db"))

    repo.record_call(
        component="kis_block_manager",
        operation="run_manager_once",
        model="gpt-5.5",
        mode="command",
        status="ok",
        latency_ms=1234,
        prompt_tokens=100,
        completion_tokens=25,
        total_tokens=125,
        usage_source="exact",
        input_chars=800,
        output_chars=200,
        metadata={"block_count": 3},
        started_at="2026-05-13T00:00:00+00:00",
        finished_at="2026-05-13T00:00:01+00:00",
    )

    summary = repo.daily_summary("2026-05-13")

    assert summary["trading_day"] == "2026-05-13"
    assert summary["total"]["call_count"] == 1
    assert summary["total"]["total_tokens"] == 125
    assert summary["total"]["exact_token_count"] == 1
    assert summary["by_component"][0]["component"] == "kis_block_manager"
    assert summary["by_component"][0]["total_tokens"] == 125
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_llm_usage.py -q
```

Expected: fails because `tradecraft.services.llm_usage` does not exist.

- [ ] **Step 3: Implement repository**

Create `src/tradecraft/services/llm_usage.py` with:

```python
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def trading_day_from_iso(value: str) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST).date().isoformat()


def estimate_tokens(text: Any) -> int:
    raw = str(text or "")
    if not raw:
        return 0
    ascii_words = re.findall(r"[A-Za-z0-9_./:-]+", raw)
    non_ascii_chars = len(re.findall(r"[^\x00-\x7F\s]", raw))
    punctuation_chunks = len(re.findall(r"[{}\\[\\](),:;\"']", raw))
    by_chars = max(len(raw) // 4, 1)
    by_parts = len(ascii_words) + max(non_ascii_chars // 2, 0) + punctuation_chunks // 3
    return max(by_chars, by_parts, 1)


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


class LLMUsageRepository:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    trading_day TEXT NOT NULL,
                    component TEXT NOT NULL,
                    operation TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    usage_source TEXT NOT NULL DEFAULT 'missing',
                    input_chars INTEGER NOT NULL DEFAULT 0,
                    output_chars INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_day_component "
                "ON llm_calls(trading_day, component, started_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_model "
                "ON llm_calls(model, trading_day)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_status "
                "ON llm_calls(status, trading_day)"
            )

    def record_call(
        self,
        *,
        component: str,
        operation: str = "",
        model: str,
        mode: str,
        status: str,
        latency_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        usage_source: str,
        input_chars: int,
        output_chars: int,
        error_message: str = "",
        metadata: dict[str, Any] | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> dict[str, Any]:
        started = started_at or utc_now_iso()
        finished = finished_at or utc_now_iso()
        trading_day = trading_day_from_iso(started)
        row = {
            "started_at": started,
            "finished_at": finished,
            "trading_day": trading_day,
            "component": str(component or "unknown"),
            "operation": str(operation or ""),
            "model": str(model or ""),
            "mode": str(mode or ""),
            "status": str(status or "unknown"),
            "latency_ms": max(int(latency_ms or 0), 0),
            "prompt_tokens": max(int(prompt_tokens or 0), 0),
            "completion_tokens": max(int(completion_tokens or 0), 0),
            "total_tokens": max(int(total_tokens or 0), 0),
            "usage_source": str(usage_source or "missing"),
            "input_chars": max(int(input_chars or 0), 0),
            "output_chars": max(int(output_chars or 0), 0),
            "error_message": str(error_message or "")[:1200],
            "metadata_json": _json_dumps(metadata or {}),
        }
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO llm_calls (
                    started_at, finished_at, trading_day, component, operation,
                    model, mode, status, latency_ms, prompt_tokens,
                    completion_tokens, total_tokens, usage_source, input_chars,
                    output_chars, error_message, metadata_json
                )
                VALUES (
                    :started_at, :finished_at, :trading_day, :component, :operation,
                    :model, :mode, :status, :latency_ms, :prompt_tokens,
                    :completion_tokens, :total_tokens, :usage_source, :input_chars,
                    :output_chars, :error_message, :metadata_json
                )
                """,
                row,
            )
            row["id"] = int(cursor.lastrowid)
        return row

    def daily_summary(self, trading_day: str) -> dict[str, Any]:
        day = str(trading_day or datetime.now(KST).date().isoformat())
        with self._connect() as conn:
            total = conn.execute(
                """
                SELECT
                    COUNT(*) AS call_count,
                    SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
                    SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS error_count,
                    SUM(prompt_tokens) AS prompt_tokens,
                    SUM(completion_tokens) AS completion_tokens,
                    SUM(total_tokens) AS total_tokens,
                    SUM(CASE WHEN usage_source = 'exact' THEN 1 ELSE 0 END) AS exact_token_count,
                    SUM(CASE WHEN usage_source = 'estimated' THEN 1 ELSE 0 END) AS estimated_token_count,
                    AVG(latency_ms) AS avg_latency_ms
                FROM llm_calls
                WHERE trading_day = ?
                """,
                (day,),
            ).fetchone()
            by_component = conn.execute(
                """
                SELECT
                    component,
                    COUNT(*) AS call_count,
                    SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
                    SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS error_count,
                    SUM(prompt_tokens) AS prompt_tokens,
                    SUM(completion_tokens) AS completion_tokens,
                    SUM(total_tokens) AS total_tokens,
                    SUM(CASE WHEN usage_source = 'exact' THEN 1 ELSE 0 END) AS exact_token_count,
                    SUM(CASE WHEN usage_source = 'estimated' THEN 1 ELSE 0 END) AS estimated_token_count,
                    AVG(latency_ms) AS avg_latency_ms
                FROM llm_calls
                WHERE trading_day = ?
                GROUP BY component
                ORDER BY total_tokens DESC, call_count DESC
                """,
                (day,),
            ).fetchall()
        return {
            "status": "ok",
            "trading_day": day,
            "total": self._summary_row(total),
            "by_component": [self._summary_row(row) | {"component": row["component"]} for row in by_component],
        }

    @staticmethod
    def _summary_row(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {
                "call_count": 0,
                "ok_count": 0,
                "error_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "exact_token_count": 0,
                "estimated_token_count": 0,
                "avg_latency_ms": 0,
            }
        return {
            "call_count": int(row["call_count"] or 0),
            "ok_count": int(row["ok_count"] or 0),
            "error_count": int(row["error_count"] or 0),
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "exact_token_count": int(row["exact_token_count"] or 0),
            "estimated_token_count": int(row["estimated_token_count"] or 0),
            "avg_latency_ms": int(row["avg_latency_ms"] or 0),
        }
```

- [ ] **Step 4: Run repository tests**

Run:

```bash
pytest tests/test_llm_usage.py -q
```

Expected: pass.

---

## Task 2: Record Usage in CodexNativeRuntime

**Files:**
- Modify: `src/tradecraft/services/codex_native.py`
- Test: `tests/test_codex_native.py`

- [ ] **Step 1: Write failing bridge telemetry tests**

Append to `tests/test_codex_native.py`:

```python
def test_codex_runtime_records_exact_usage(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            captured["stdin"] = input or b""
            return (
                b'{"content":"ok","usage":{"input_tokens":12,"output_tokens":3,"total_tokens":15}}',
                b"",
            )

        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        _ = (args, kwargs)
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            command="/tmp/mock-wrapper",
            usage_db_path=str(tmp_path / "llm_usage.db"),
            usage_component="kis_block_manager",
        )
    )

    result = asyncio.run(
        bridge.complete(
            payload={
                "messages": [{"role": "user", "content": "hello"}],
                "telemetry": {"operation": "run_manager_once"},
            }
        )
    )

    assert result["usage"]["total_tokens"] == 15
    from tradecraft.services.llm_usage import LLMUsageRepository

    summary = LLMUsageRepository(str(tmp_path / "llm_usage.db")).daily_summary("2026-05-13")
    assert summary["total"]["call_count"] == 1
    assert summary["total"]["total_tokens"] == 15
    assert summary["by_component"][0]["component"] == "kis_block_manager"


def test_codex_runtime_records_estimated_usage_when_provider_usage_missing(tmp_path, monkeypatch) -> None:
    class _Proc:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            _ = input
            return (b'{"content":"estimated output"}', b"")

        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        _ = (args, kwargs)
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            command="/tmp/mock-wrapper",
            usage_db_path=str(tmp_path / "llm_usage.db"),
            usage_component="investment_memory",
        )
    )

    asyncio.run(bridge.complete(payload={"messages": [{"role": "user", "content": "삼성전자 판단"}]}))

    from tradecraft.services.llm_usage import LLMUsageRepository

    summary = LLMUsageRepository(str(tmp_path / "llm_usage.db")).daily_summary("2026-05-13")
    assert summary["total"]["call_count"] == 1
    assert summary["total"]["estimated_token_count"] == 1
    assert summary["total"]["total_tokens"] > 0
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_codex_native.py::test_codex_runtime_records_exact_usage tests/test_codex_native.py::test_codex_runtime_records_estimated_usage_when_provider_usage_missing -q
```

Expected: fails because config fields do not exist.

- [ ] **Step 3: Extend `CodexNativeConfig`**

Add fields:

```python
    usage_enabled: bool = True
    usage_db_path: str = ".runtime/llm_usage.db"
    usage_component: str = "unknown"
```

- [ ] **Step 4: Record in `complete()`**

In `CodexNativeRuntime.complete()`, measure started/finished time and call a private method after success/failure:

```python
        started_at = datetime.now(timezone.utc)
        try:
            if self.mode == "command":
                raw = await self._call_command(payload, timeout_ms=timeout_ms)
            else:
                raw = await self._call_url(payload, timeout_ms=timeout_ms)
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            self._record_usage(
                payload=payload,
                result=None,
                status="error",
                error_message=str(exc),
                started_at=started_at,
                finished_at=finished_at,
            )
            return {"ok": False, "mode": self.mode, "error": str(exc)}

        normalized = self._normalize(raw)
        normalized["ok"] = True
        normalized["mode"] = self.mode
        finished_at = datetime.now(timezone.utc)
        self._record_usage(
            payload=payload,
            result=normalized,
            status="ok",
            error_message="",
            started_at=started_at,
            finished_at=finished_at,
        )
        return normalized
```

Add imports:

```python
from datetime import datetime, timezone

from tradecraft.services.llm_usage import LLMUsageRepository, estimate_tokens
```

Add helper:

```python
    def _record_usage(
        self,
        *,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        status: str,
        error_message: str,
        started_at: datetime,
        finished_at: datetime,
    ) -> None:
        if not self.config.usage_enabled:
            return
        telemetry = payload.get("telemetry") if isinstance(payload.get("telemetry"), dict) else {}
        component = str(telemetry.get("component") or self.config.usage_component or "unknown")
        operation = str(telemetry.get("operation") or payload.get("operation") or "")
        input_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        output_text = str((result or {}).get("content") or "")
        usage = (result or {}).get("usage") if isinstance((result or {}).get("usage"), dict) else None
        if usage:
            prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
            usage_source = "exact"
        elif status == "ok":
            prompt_tokens = estimate_tokens(input_text)
            completion_tokens = estimate_tokens(output_text)
            total_tokens = prompt_tokens + completion_tokens
            usage_source = "estimated"
        else:
            prompt_tokens = estimate_tokens(input_text)
            completion_tokens = 0
            total_tokens = prompt_tokens
            usage_source = "estimated" if prompt_tokens > 0 else "missing"
        repo = LLMUsageRepository(self.config.usage_db_path)
        repo.record_call(
            component=component,
            operation=operation,
            model=self.resolved_model,
            mode=self.mode,
            status=status,
            latency_ms=int((finished_at - started_at).total_seconds() * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            usage_source=usage_source,
            input_chars=len(input_text),
            output_chars=len(output_text),
            error_message=error_message,
            metadata={
                "timeout_ms": self.config.timeout_ms,
                "payload_keys": sorted(str(key) for key in payload.keys()),
            },
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
        )
```

- [ ] **Step 5: Run bridge tests**

Run:

```bash
pytest tests/test_codex_native.py tests/test_llm_usage.py -q
```

Expected: pass.

---

## Task 3: Settings and Component Wiring

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify bridge construction sites in `src/tradecraft/main.py` and runtime/service files listed in File Structure.
- Test: `tests/test_config.py`

- [ ] **Step 1: Add config test**

Append:

```python
def test_llm_usage_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_LLM_USAGE_ENABLED", raising=False)
    monkeypatch.delenv("TRADECRAFT_LLM_USAGE_DB_PATH", raising=False)
    settings = AppSettings()
    assert settings.llm_usage_enabled is True
    assert settings.llm_usage_db_path == ".runtime/llm_usage.db"
```

- [ ] **Step 2: Add settings**

In `AppSettings`:

```python
    llm_usage_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_LLM_USAGE_ENABLED",
    )
    llm_usage_db_path: str = Field(
        default=".runtime/llm_usage.db",
        alias="TRADECRAFT_LLM_USAGE_DB_PATH",
    )
```

- [ ] **Step 3: Wire component labels**

Every `CodexNativeConfig(...)` should include:

```python
usage_enabled=settings.llm_usage_enabled,
usage_db_path=settings.llm_usage_db_path,
usage_component="<component_name>",
```

Use these labels:

- main helper/research ask bridge: `research_ask`
- strategy intelligence bridge: `strategy_intelligence`
- market judge runner/main engine bridge: `market_judge`
- KIS block trader bridge: `kis_block_manager`
- investment memory bridge: `investment_memory`
- naver report facts bridge: `research_reports`
- research pipeline bridge: `research_pipeline`
- portfolio coach bridge: `portfolio_coach`

- [ ] **Step 4: Run config and focused import tests**

Run:

```bash
pytest tests/test_config.py::test_llm_usage_defaults tests/test_codex_native.py tests/test_llm_usage.py -q
python3 -m py_compile src/tradecraft/main.py src/tradecraft/runtime/kis_block_trader_runner.py src/tradecraft/runtime/investment_memory_runner.py src/tradecraft/runtime/market_judge_runner.py src/tradecraft/runtime/research_runner.py src/tradecraft/runtime/strategy_insights_runner.py
```

Expected: pass.

---

## Task 4: API Endpoints

**Files:**
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_api_smoke.py`

- [ ] **Step 1: Write API tests**

Add:

```python
def test_llm_usage_summary_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")
    monkeypatch.setattr(settings, "llm_usage_db_path", str(tmp_path / "llm_usage.db"))
    from tradecraft.services.llm_usage import LLMUsageRepository

    LLMUsageRepository(settings.llm_usage_db_path).record_call(
        component="market_judge",
        operation="run_once",
        model="gpt-5.5",
        mode="command",
        status="ok",
        latency_ms=10,
        prompt_tokens=20,
        completion_tokens=5,
        total_tokens=25,
        usage_source="exact",
        input_chars=100,
        output_chars=20,
        started_at="2026-05-13T00:00:00+00:00",
        finished_at="2026-05-13T00:00:01+00:00",
    )

    response = client.get(
        "/api/llm/usage/summary?trading_day=2026-05-13",
        headers={"Authorization": "Bearer test-admin"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"]["total_tokens"] == 25
    assert payload["by_component"][0]["component"] == "market_judge"
```

- [ ] **Step 2: Add endpoint**

In `main.py`, import repository:

```python
from tradecraft.services.llm_usage import LLMUsageRepository
```

Add helper:

```python
def llm_usage_repository() -> LLMUsageRepository:
    return LLMUsageRepository(settings.llm_usage_db_path)
```

Add protected routes:

```python
@app.get("/api/llm/usage/summary")
def llm_usage_summary(
    trading_day: str = "",
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    day = trading_day or datetime.now(KST).date().isoformat()
    return llm_usage_repository().daily_summary(day)
```

Optional v1 detail route:

```python
@app.get("/api/llm/usage/status")
def llm_usage_status(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    repo = llm_usage_repository()
    today = datetime.now(KST).date().isoformat()
    summary = repo.daily_summary(today)
    return {
        "status": "ok",
        "enabled": bool(settings.llm_usage_enabled),
        "db_path": settings.llm_usage_db_path,
        "today": summary,
    }
```

- [ ] **Step 3: Run API tests**

Run:

```bash
pytest tests/test_api_smoke.py::test_llm_usage_summary_endpoint -q
```

Expected: pass.

---

## Task 5: UI Daily Usage Panel

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`

- [ ] **Step 1: Add state and loader**

In `state`, add:

```js
  llmUsage: null,
  llmUsageError: "",
```

Add loader:

```js
async function loadLLMUsage() {
  try {
    state.llmUsage = await getJSON("/llm/usage/summary");
    state.llmUsageError = "";
  } catch (error) {
    state.llmUsageError = getErrorMessage(error);
  }
}
```

- [ ] **Step 2: Add renderer**

Add:

```js
function renderLLMUsagePanel() {
  const payload = state.llmUsage;
  if (!payload) {
    return `<section class="memory-section"><h3>LLM 사용량</h3><div class="notice">${escapeHTML(state.llmUsageError || "LLM 사용량 집계 대기")}</div></section>`;
  }
  const total = payload.total || {};
  const rows = Array.isArray(payload.by_component) ? payload.by_component : [];
  return `
    <section class="memory-section">
      <div class="panel-head compact">
        <h3>LLM 사용량</h3>
        <p>${escapeHTML(payload.trading_day || "")} · gpt 호출 계량</p>
      </div>
      <div class="strategy-intel-metrics">
        <span><b>${escapeHTML(fmtKRW(total.call_count || 0))}</b>호출</span>
        <span><b>${escapeHTML(fmtKRW(total.total_tokens || 0))}</b>총 토큰</span>
        <span><b>${escapeHTML(fmtKRW(total.estimated_token_count || 0))}</b>추정 집계</span>
        <span><b>${escapeHTML(fmtKRW(total.error_count || 0))}</b>실패</span>
      </div>
      <div class="table-wrap compact">
        <table>
          <thead><tr><th>컴포넌트</th><th>호출</th><th>토큰</th><th>입력</th><th>출력</th></tr></thead>
          <tbody>
            ${rows.length ? rows.map((row) => `
              <tr>
                <td>${escapeHTML(row.component || "-")}</td>
                <td>${escapeHTML(fmtKRW(row.call_count || 0))}</td>
                <td>${escapeHTML(fmtKRW(row.total_tokens || 0))}</td>
                <td>${escapeHTML(fmtKRW(row.prompt_tokens || 0))}</td>
                <td>${escapeHTML(fmtKRW(row.completion_tokens || 0))}</td>
              </tr>
            `).join("") : '<tr><td colspan="5">오늘 LLM 호출 없음</td></tr>'}
          </tbody>
        </table>
      </div>
    </section>
  `;
}
```

- [ ] **Step 3: Insert panel**

Place `renderLLMUsagePanel()` in the System/운영 tab, near ops readiness. Also place a compact token count chip in the 블록 트레이딩 readiness area if `state.llmUsage?.total?.total_tokens` exists.

- [ ] **Step 4: Static checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
git diff --check -- src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css
```

Expected: pass.

---

## Task 6: Telegram Daily Summary

**Files:**
- Modify: `src/tradecraft/services/telegram_cli.py`
- Test: `tests/test_telegram_cli.py`

- [ ] **Step 1: Add Telegram formatter test**

Add:

```python
def test_llm_usage_text_groups_components() -> None:
    cli = TelegramCLI(lambda: {})
    text = cli.llm_usage_text(
        {
            "trading_day": "2026-05-13",
            "total": {
                "call_count": 3,
                "total_tokens": 1200,
                "prompt_tokens": 900,
                "completion_tokens": 300,
                "estimated_token_count": 1,
                "error_count": 0,
            },
            "by_component": [
                {"component": "kis_block_manager", "call_count": 2, "total_tokens": 900},
                {"component": "investment_memory", "call_count": 1, "total_tokens": 300},
            ],
        }
    )
    assert "LLM 사용량" in text
    assert "1,200" in text
    assert "kis_block_manager" in text
    assert "investment_memory" in text
```

- [ ] **Step 2: Add formatter**

Add method:

```python
    def llm_usage_text(self, payload: dict[str, Any]) -> str:
        total = payload.get("total") if isinstance(payload.get("total"), dict) else {}
        rows = payload.get("by_component") if isinstance(payload.get("by_component"), list) else []
        lines = [
            f"LLM 사용량 · {payload.get('trading_day') or '-'}",
            f"- 호출 {fmt_num(total.get('call_count'))}회",
            f"- 총 토큰 {fmt_num(total.get('total_tokens'))}",
            f"- 입력/출력 {fmt_num(total.get('prompt_tokens'))} / {fmt_num(total.get('completion_tokens'))}",
            f"- 추정 집계 {fmt_num(total.get('estimated_token_count'))}건 · 실패 {fmt_num(total.get('error_count'))}건",
        ]
        for row in rows[:8]:
            lines.append(
                f"- {row.get('component') or '-'}: {fmt_num(row.get('call_count'))}회 · {fmt_num(row.get('total_tokens'))} tokens"
            )
        return "\n".join(lines)
```

Use the existing number formatter name in `telegram_cli.py`; if it is `_fmt_num`, use that instead of `fmt_num`.

- [ ] **Step 3: Wire command in main webhook/polling**

In Telegram command handling, add `/llm-usage` and call `/api` equivalent repository summary for today.

- [ ] **Step 4: Run Telegram tests**

Run:

```bash
pytest tests/test_telegram_cli.py::test_llm_usage_text_groups_components -q
```

Expected: pass.

---

## Task 7: Memory/End-of-Day Integration

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Add memory context test**

Add:

```python
def test_post_close_context_can_include_llm_usage(tmp_path) -> None:
    service = InvestmentMemoryService(
        InvestmentMemoryConfig(root_path=str(tmp_path / "memory"), db_path=str(tmp_path / "memory.db")),
        codex_runtime=_FakeCodexNativeRuntime(),
    )
    context = service.build_ritual_context(
        slot="post_close",
        trading_day="2026-05-13",
        account={"cash_krw": 1_000_000},
        blocks={"blocks": []},
        llm_usage={
            "total": {"call_count": 3, "total_tokens": 1200},
            "by_component": [{"component": "kis_block_manager", "total_tokens": 900}],
        },
    )
    assert context["llm_usage"]["total"]["total_tokens"] == 1200
```

- [ ] **Step 2: Add optional `llm_usage` field to ritual context**

Where ritual context is built in `main.py`, attach:

```python
context["llm_usage"] = llm_usage_repository().daily_summary(trading_day)
```

Keep this field compact: total plus top 8 components only.

- [ ] **Step 3: Post-close message uses it**

In deterministic fallback/post-close prompt context, include a short section:

```text
오늘 LLM 호출/토큰: {call_count}회 / {total_tokens} tokens.
가장 많이 쓴 컴포넌트: {top_component}.
```

- [ ] **Step 4: Run memory tests**

Run:

```bash
pytest tests/test_investment_memory.py -q
```

Expected: pass.

---

## Task 8: Verification and Runtime Rollout

**Files:** no new files unless tests expose issues.

- [ ] **Step 1: Run focused tests**

```bash
pytest tests/test_llm_usage.py tests/test_codex_native.py tests/test_api_smoke.py tests/test_telegram_cli.py tests/test_investment_memory.py -q
```

Expected: pass.

- [ ] **Step 2: Run static checks**

```bash
ruff check src/tradecraft/services/llm_usage.py src/tradecraft/services/codex_native.py src/tradecraft/services/telegram_cli.py src/tradecraft/main.py tests/test_llm_usage.py tests/test_codex_native.py tests/test_api_smoke.py tests/test_telegram_cli.py
node --check src/tradecraft/web/static/app.js
git diff --check
```

Expected: pass.

- [ ] **Step 3: Restart affected runtimes**

```bash
tmux kill-session -t hermes-control 2>/dev/null || true
tmux kill-session -t hermes-kis-block-trader 2>/dev/null || true
tmux kill-session -t hermes-investment-memory 2>/dev/null || true
tmux kill-session -t hermes-market-judge 2>/dev/null || true

tmux new-session -d -s hermes-control 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m uvicorn tradecraft.main:app --host 127.0.0.1 --port 18080 2>&1 | tee -a .runtime/tradecraft-control-18080.log'
tmux new-session -d -s hermes-kis-block-trader 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m tradecraft.runtime.kis_block_trader_runner 2>&1 | tee -a .runtime/kis-block-trader.log'
tmux new-session -d -s hermes-investment-memory 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m tradecraft.runtime.investment_memory_runner 2>&1 | tee -a .runtime/investment-memory.log'
tmux new-session -d -s hermes-market-judge 'cd /Users/juhwan/hermes_v2 && .venv/bin/python -m tradecraft.runtime.market_judge_runner 2>&1 | tee -a .runtime/market-judge.log'
```

- [ ] **Step 4: Smoke endpoints**

```bash
curl -fsS http://127.0.0.1:18080/api/health
curl -fsS -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" "http://127.0.0.1:18080/api/llm/usage/summary"
curl -fsS -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" "http://127.0.0.1:18080/api/ops/readiness"
```

Expected:
- health is `ok`
- usage summary returns `status: ok`
- readiness is green or has only known non-blocking warnings

---

## Notes

- Exact token usage depends on what the current Codex native runtime/wrapper returns. If usage is absent, store `usage_source="estimated"` and make that visible in UI/Telegram.
- Do not hardcode model pricing. If cost is needed later, add env-driven model price settings because prices can change.
- This feature should never block trading. If usage recording fails, log it and allow `CodexNativeRuntime.complete()` to return normally.
