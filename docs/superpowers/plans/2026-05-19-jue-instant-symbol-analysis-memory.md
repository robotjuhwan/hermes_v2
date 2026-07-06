# Jue Instant Symbol Analysis Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an instant per-symbol analysis layer where 쥬 gathers current data, produces qualitative gpt-5.5 evaluations, stores them as symbol history, and reuses them in future block trading decisions.

**Architecture:** Add a focused `SymbolAnalysisService` on top of existing KIS quotes, Naver/WiseReport fundamentals, Naver reports/RAG, block ledger, and investment memory. Persist analysis runs in `investment_memory.db`, mirror important conclusions into `.runtime/investment_memory/symbols/{symbol}.md`, expose admin-gated API/Telegram/UI entry points, and inject recent symbol analyses into `context_pack()` for the block manager and market judge.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, existing `CodexNativeRuntime`, existing KIS/Naver/RAG services, static JS frontend, pytest.

---

## File Structure

- Create `src/tradecraft/services/symbol_analysis.py`
  - Owns instant analysis orchestration, prompt construction, LLM response parsing, special-watch detection, and persistence calls.
- Modify `src/tradecraft/services/investment_memory.py`
  - Adds `symbol_analyses` and `special_watch_symbols` schema, repository methods, Markdown mirroring, context-pack injection, status summaries.
- Modify `src/tradecraft/main.py`
  - Wires `SymbolAnalysisService`, adds admin-gated APIs, adds Telegram commands.
- Modify `src/tradecraft/services/kis_block_trader.py`
  - Injects recent symbol analyses into manager context and triggers special-watch analysis after adopting existing holdings.
- Modify `src/tradecraft/services/market_judgment.py`
  - Includes recent symbol analyses when judging focus symbols.
- Modify `src/tradecraft/web/static/app.js`
  - Adds instant symbol analysis panel, history rendering, and special-watch indicators.
- Modify `src/tradecraft/web/static/index.html`
  - Cache-bust static asset version.
- Add tests:
  - `tests/test_symbol_analysis.py`
  - Extend `tests/test_investment_memory.py`
  - Extend `tests/test_kis_block_trader.py`
  - Extend `tests/test_api_smoke.py`
  - Extend `tests/test_telegram_cli.py`

---

### Task 1: Persist Symbol Analysis History In Investment Memory

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Write failing repository tests**

Append these tests to `tests/test_investment_memory.py`:

```python
def test_symbol_analysis_history_is_persisted_and_listed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    saved = service.repository.save_symbol_analysis(
        {
            "symbol": "033790",
            "name": "피노",
            "trigger": "user_request",
            "source": "instant",
            "model": "gpt-5.5",
            "status": "ok",
            "summary": "피노는 단기 반등 블록만 허용한다.",
            "short_view": "단기 변동성 대응",
            "mid_view": "재무 공백 확인",
            "long_view": "중장기 확신 부족",
            "stance": "risk_check",
            "confidence": 0.72,
            "reasons": ["목표가 회복 확인"],
            "risks": ["밸류 공백"],
            "data_gaps": ["리포트 없음"],
            "triggers": ["13800원 회복"],
            "target_candidates": [13800],
            "stop_candidates": [13340],
            "snapshot": {"quote": {"price": 13660}},
        }
    )

    history = service.repository.list_symbol_analyses("033790", limit=5)

    assert saved["id"] > 0
    assert history["status"] == "ok"
    assert history["symbol"] == "033790"
    assert history["count"] == 1
    assert history["items"][0]["summary"] == "피노는 단기 반등 블록만 허용한다."
    assert history["items"][0]["reasons"] == ["목표가 회복 확인"]
    assert history["items"][0]["snapshot"]["quote"]["price"] == 13660


def test_symbol_analysis_updates_symbol_markdown(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    service.record_symbol_analysis_memory(
        {
            "symbol": "033790",
            "name": "피노",
            "summary": "피노는 데이터 공백이 커서 짧은 블록만 다룬다.",
            "stance": "risk_check",
            "confidence": 0.68,
            "created_at": "2026-05-19T11:30:00+00:00",
        }
    )

    text = (tmp_path / "memory" / "symbols" / "033790.md").read_text(encoding="utf-8")

    assert "피노" in text
    assert "데이터 공백" in text
    assert "risk_check" in text
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_investment_memory.py::test_symbol_analysis_history_is_persisted_and_listed tests/test_investment_memory.py::test_symbol_analysis_updates_symbol_markdown -q
```

Expected: failure because `save_symbol_analysis`, `list_symbol_analyses`, and `record_symbol_analysis_memory` do not exist.

- [ ] **Step 3: Add SQLite schema**

In `InvestmentMemoryRepository._ensure_schema()`, add:

```python
                CREATE TABLE IF NOT EXISTS symbol_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    trigger TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'instant',
                    model TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ok',
                    summary TEXT NOT NULL DEFAULT '',
                    short_view TEXT NOT NULL DEFAULT '',
                    mid_view TEXT NOT NULL DEFAULT '',
                    long_view TEXT NOT NULL DEFAULT '',
                    stance TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    risks_json TEXT NOT NULL DEFAULT '[]',
                    data_gaps_json TEXT NOT NULL DEFAULT '[]',
                    triggers_json TEXT NOT NULL DEFAULT '[]',
                    target_candidates_json TEXT NOT NULL DEFAULT '[]',
                    stop_candidates_json TEXT NOT NULL DEFAULT '[]',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    prompt_json TEXT NOT NULL DEFAULT '{}',
                    raw_response_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
```

Add indexes:

```python
                CREATE INDEX IF NOT EXISTS idx_symbol_analyses_symbol_created
                    ON symbol_analyses(symbol, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_symbol_analyses_trigger_created
                    ON symbol_analyses(trigger, created_at DESC);
```

- [ ] **Step 4: Add repository methods**

Add methods to `InvestmentMemoryRepository`:

```python
    def save_symbol_analysis(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        symbol = str(payload.get("symbol") or "").strip()
        if not _is_symbol(symbol):
            raise ValueError("symbol must be a 6-digit KRX code")
        row = {
            "symbol": symbol,
            "name": _clean_text(payload.get("name"), limit=80),
            "trigger": _clean_text(payload.get("trigger"), limit=80),
            "source": _clean_text(payload.get("source") or "instant", limit=80),
            "model": _clean_text(payload.get("model"), limit=80),
            "status": _clean_text(payload.get("status") or "ok", limit=40),
            "summary": _clean_text(payload.get("summary"), limit=2000),
            "short_view": _clean_text(payload.get("short_view"), limit=1200),
            "mid_view": _clean_text(payload.get("mid_view"), limit=1200),
            "long_view": _clean_text(payload.get("long_view"), limit=1200),
            "stance": _clean_text(payload.get("stance"), limit=80),
            "confidence": _safe_float(payload.get("confidence")),
            "reasons_json": _json_dumps(list(payload.get("reasons") or [])[:8]),
            "risks_json": _json_dumps(list(payload.get("risks") or [])[:8]),
            "data_gaps_json": _json_dumps(list(payload.get("data_gaps") or [])[:8]),
            "triggers_json": _json_dumps(list(payload.get("triggers") or [])[:8]),
            "target_candidates_json": _json_dumps(list(payload.get("target_candidates") or [])[:8]),
            "stop_candidates_json": _json_dumps(list(payload.get("stop_candidates") or [])[:8]),
            "snapshot_json": _json_dumps(payload.get("snapshot") or {}),
            "prompt_json": _json_dumps(payload.get("prompt") or {}),
            "raw_response_json": _json_dumps(payload.get("raw_response") or {}),
            "error_message": _compact_error_message(payload.get("error_message"), limit=800),
            "created_at": _clean_text(payload.get("created_at") or now, limit=80),
            "updated_at": now,
        }
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO symbol_analyses (
                    symbol, name, trigger, source, model, status, summary,
                    short_view, mid_view, long_view, stance, confidence,
                    reasons_json, risks_json, data_gaps_json, triggers_json,
                    target_candidates_json, stop_candidates_json, snapshot_json,
                    prompt_json, raw_response_json, error_message, created_at, updated_at
                ) VALUES (
                    :symbol, :name, :trigger, :source, :model, :status, :summary,
                    :short_view, :mid_view, :long_view, :stance, :confidence,
                    :reasons_json, :risks_json, :data_gaps_json, :triggers_json,
                    :target_candidates_json, :stop_candidates_json, :snapshot_json,
                    :prompt_json, :raw_response_json, :error_message, :created_at, :updated_at
                )
                """,
                row,
            )
            conn.commit()
            row["id"] = int(cursor.lastrowid)
        return self._symbol_analysis_public(row)

    def list_symbol_analyses(self, symbol: str, *, limit: int = 10) -> dict[str, Any]:
        code = str(symbol or "").strip()
        if not _is_symbol(code):
            return {"status": "invalid_symbol", "symbol": code, "items": [], "count": 0}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM symbol_analyses
                WHERE symbol = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (code, max(min(int(limit), 50), 1)),
            ).fetchall()
        items = [self._symbol_analysis_public(dict(row)) for row in rows]
        return {"status": "ok", "symbol": code, "count": len(items), "items": items}

    @staticmethod
    def _symbol_analysis_public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row.get("id") or 0),
            "symbol": str(row.get("symbol") or ""),
            "name": str(row.get("name") or ""),
            "trigger": str(row.get("trigger") or ""),
            "source": str(row.get("source") or ""),
            "model": str(row.get("model") or ""),
            "status": str(row.get("status") or ""),
            "summary": str(row.get("summary") or ""),
            "short_view": str(row.get("short_view") or ""),
            "mid_view": str(row.get("mid_view") or ""),
            "long_view": str(row.get("long_view") or ""),
            "stance": str(row.get("stance") or ""),
            "confidence": _safe_float(row.get("confidence")),
            "reasons": _json_loads(row.get("reasons_json"), []),
            "risks": _json_loads(row.get("risks_json"), []),
            "data_gaps": _json_loads(row.get("data_gaps_json"), []),
            "triggers": _json_loads(row.get("triggers_json"), []),
            "target_candidates": _json_loads(row.get("target_candidates_json"), []),
            "stop_candidates": _json_loads(row.get("stop_candidates_json"), []),
            "snapshot": _json_loads(row.get("snapshot_json"), {}),
            "error_message": str(row.get("error_message") or ""),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }
```

- [ ] **Step 5: Add service-level Markdown mirroring**

Add to `InvestmentMemoryService`:

```python
    def record_symbol_analysis_memory(self, analysis: dict[str, Any]) -> dict[str, Any]:
        symbol = str(analysis.get("symbol") or "").strip()
        if not _is_symbol(symbol):
            return {"status": "invalid_symbol", "symbol": symbol}
        root = self.root_path / "symbols"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{symbol}.md"
        if not path.exists():
            path.write_text(f"# {symbol}.md\n\n", encoding="utf-8")
        created_at = _clean_text(analysis.get("created_at") or utc_now_iso(), limit=80)
        name = _clean_text(analysis.get("name"), limit=80) or symbol
        summary = _clean_text(analysis.get("summary"), limit=900)
        stance = _clean_text(analysis.get("stance"), limit=60)
        confidence = _safe_float(analysis.get("confidence"))
        entry = (
            f"\n## {created_at} · instant analysis\n\n"
            f"{name}({symbol}) · {stance} · confidence {confidence:.2f}\n\n"
            f"{summary}\n"
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        return {"status": "ok", "symbol": symbol, "path": str(path)}
```

- [ ] **Step 6: Run tests and verify pass**

Run:

```bash
pytest tests/test_investment_memory.py::test_symbol_analysis_history_is_persisted_and_listed tests/test_investment_memory.py::test_symbol_analysis_updates_symbol_markdown -q
```

Expected: both tests pass.

---

### Task 2: Add SymbolAnalysisService For Instant Qualitative Analysis

**Files:**
- Create: `src/tradecraft/services/symbol_analysis.py`
- Test: `tests/test_symbol_analysis.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_symbol_analysis.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tradecraft.services.investment_memory import InvestmentMemoryConfig, InvestmentMemoryService
from tradecraft.services.symbol_analysis import SymbolAnalysisService


class _FakeLLM:
    ready = True
    resolved_model = "gpt-5.5"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(self, payload: dict[str, Any], timeout_ms: int | None = None) -> dict[str, Any]:
        self.calls.append(payload)
        return {
            "ok": True,
            "content": json.dumps(
                {
                    "summary": "피노는 급락 후 단기 블록만 가능한 고변동 종목이다.",
                    "stance": "risk_check",
                    "confidence": 0.74,
                    "short_view": "단기 반등 확인",
                    "mid_view": "재무 공백 보강 필요",
                    "long_view": "중장기 확신 부족",
                    "reasons": ["당일 저점 방어"],
                    "risks": ["EPS 음수", "PBR 부담"],
                    "data_gaps": ["리포트 없음"],
                    "triggers": ["13800원 회복"],
                    "target_candidates": [13800],
                    "stop_candidates": [13340],
                },
                ensure_ascii=False,
            ),
        }


class _FakeFundamentals:
    def __init__(self) -> None:
        self.collected: list[tuple[list[str], bool]] = []

    async def collect_symbols(self, symbols: list[str], *, force: bool = False) -> dict[str, Any]:
        self.collected.append((symbols, force))
        return {"status": "ok", "items": [{"symbol": symbols[0], "status": "ok"}]}

    def latest(self, symbol: str) -> dict[str, Any]:
        return {
            "status": "ok",
            "symbol": symbol,
            "name": "피노",
            "valuation": {"pbr": 9.19, "eps": -135},
            "score": {"risks": ["PBR 9.19배 부담", "EPS가 0 이하"]},
        }


class _FakeQuoteProvider:
    async def fetch_quote(self, symbol: str) -> dict[str, Any]:
        return {"status": "ok", "symbol": symbol, "name": "피노", "price": 13660, "change_pct": -8.32}


class _FakeReports:
    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        return {symbols[0]: "피노"}

    def search(self, query: str = "", symbol: str = "", limit: int = 5) -> list[dict[str, Any]]:
        return []


class _FakeRAG:
    def query(self, text: str, *, symbol: str = "", limit: int = 5) -> list[dict[str, Any]]:
        return []


class _FakeBlocks:
    def blocks(self) -> dict[str, Any]:
        return {
            "blocks": [
                {
                    "block_id": "blk_033790_1",
                    "symbol": "033790",
                    "name": "피노",
                    "status": "closed",
                    "entry_price": 13660,
                    "target_price": 13800,
                    "stop_price": 13340,
                }
            ]
        }


@pytest.mark.asyncio
async def test_run_instant_analysis_collects_fundamentals_and_persists(tmp_path: Path) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    llm = _FakeLLM()
    fundamentals = _FakeFundamentals()
    service = SymbolAnalysisService(
        codex_runtime=llm,  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=fundamentals,  # type: ignore[arg-type]
        quote_provider=_FakeQuoteProvider(),  # type: ignore[arg-type]
        report_repository=_FakeReports(),  # type: ignore[arg-type]
        rag_store=_FakeRAG(),  # type: ignore[arg-type]
        block_provider=_FakeBlocks(),  # type: ignore[arg-type]
    )

    result = await service.run("033790", trigger="user_request", force_collect=True)

    assert result["status"] == "ok"
    assert result["symbol"] == "033790"
    assert result["analysis"]["summary"].startswith("피노는 급락")
    assert fundamentals.collected == [(["033790"], True)]
    assert llm.calls
    assert "fundamentals" in llm.calls[0]["context"]
    history = memory.repository.list_symbol_analyses("033790")
    assert history["count"] == 1
    assert history["items"][0]["stance"] == "risk_check"
    assert (tmp_path / "memory" / "symbols" / "033790.md").exists()
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
pytest tests/test_symbol_analysis.py::test_run_instant_analysis_collects_fundamentals_and_persists -q
```

Expected: import failure because `tradecraft.services.symbol_analysis` does not exist.

- [ ] **Step 3: Create service with clear protocols**

Create `src/tradecraft/services/symbol_analysis.py`:

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from tradecraft.services.codex_runtime import CodexNativeRuntime


def _is_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _string_list(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = [value] if value else []
    return [str(item).strip()[:300] for item in items if str(item or "").strip()][:limit]


def _parse_json_content(result: dict[str, Any]) -> dict[str, Any]:
    content = str(result.get("content") or "").strip()
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            return {}
        parsed = json.loads(match.group(0))
    return parsed if isinstance(parsed, dict) else {}


class FundamentalsProvider(Protocol):
    async def collect_symbols(self, symbols: list[str], *, force: bool = False) -> dict[str, Any]: ...
    def latest(self, symbol: str) -> dict[str, Any] | None: ...


class QuoteProvider(Protocol):
    async def fetch_quote(self, symbol: str) -> dict[str, Any]: ...


class ReportRepository(Protocol):
    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]: ...
    def search(self, query: str = "", symbol: str = "", limit: int = 5) -> list[dict[str, Any]]: ...


class RAGStore(Protocol):
    def query(self, text: str, *, symbol: str = "", limit: int = 5) -> list[dict[str, Any]]: ...


class BlockProvider(Protocol):
    def blocks(self) -> dict[str, Any]: ...


@dataclass
class SymbolAnalysisService:
    codex_runtime: CodexNativeRuntime
    memory_service: Any
    fundamentals: FundamentalsProvider
    quote_provider: QuoteProvider
    report_repository: ReportRepository
    rag_store: RAGStore | None = None
    block_provider: BlockProvider | None = None
    timeout_ms: int = 45_000

    async def run(
        self,
        symbol_or_name: str,
        *,
        trigger: str = "user_request",
        force_collect: bool = True,
    ) -> dict[str, Any]:
        symbol = self._resolve_symbol(symbol_or_name)
        if not _is_symbol(symbol):
            return {"status": "invalid_symbol", "symbol": symbol_or_name}

        name = self.report_repository.resolve_symbol_names([symbol]).get(symbol, symbol)
        collect_result = await self.fundamentals.collect_symbols([symbol], force=force_collect)
        fundamentals = self.fundamentals.latest(symbol) or {"status": "missing", "symbol": symbol}
        quote = await self.quote_provider.fetch_quote(symbol)
        reports = self.report_repository.search(symbol=symbol, limit=5)
        rag_chunks = self.rag_store.query(f"{name} {symbol}", symbol=symbol, limit=5) if self.rag_store else []
        blocks = self._symbol_blocks(symbol)
        recent_history = self.memory_service.repository.list_symbol_analyses(symbol, limit=5)

        prompt = self._build_prompt(
            symbol=symbol,
            name=name,
            trigger=trigger,
            quote=quote,
            fundamentals=fundamentals,
            reports=reports,
            rag_chunks=rag_chunks,
            blocks=blocks,
            recent_history=recent_history,
        )
        analysis, raw_response = await self._call_llm(prompt)
        payload = {
            **analysis,
            "symbol": symbol,
            "name": name,
            "trigger": trigger,
            "source": "instant",
            "model": getattr(self.codex_runtime, "resolved_model", "gpt-5.5"),
            "status": "ok",
            "snapshot": {
                "quote": quote,
                "fundamentals": fundamentals,
                "fundamentals_collect": collect_result,
                "reports": reports,
                "rag_chunks": rag_chunks,
                "blocks": blocks,
                "recent_history": recent_history,
            },
            "prompt": prompt,
            "raw_response": raw_response,
        }
        saved = self.memory_service.repository.save_symbol_analysis(payload)
        self.memory_service.record_symbol_analysis_memory(saved)
        return {"status": "ok", "symbol": symbol, "name": name, "analysis": saved}

    def _resolve_symbol(self, value: str) -> str:
        text = str(value or "").strip()
        if _is_symbol(text):
            return text
        mapping = self.report_repository.resolve_symbol_names([])
        for symbol, name in mapping.items():
            if text and text == str(name):
                return str(symbol)
        return text

    def _symbol_blocks(self, symbol: str) -> list[dict[str, Any]]:
        if not self.block_provider:
            return []
        payload = self.block_provider.blocks()
        rows = payload.get("blocks") if isinstance(payload, dict) else []
        return [row for row in list(rows or []) if isinstance(row, dict) and str(row.get("symbol")) == symbol][:10]

    def _build_prompt(self, **context: Any) -> dict[str, Any]:
        return {
            "model": getattr(self.codex_runtime, "resolved_model", "gpt-5.5"),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "너는 HERMES의 투자 파트너 쥬다. 특정 종목을 실거래 블록 관점에서 분석한다. "
                        "가격, 재무, 리포트, RAG, 보유/블록, 과거 판단을 근거로 단기/중기/장기 평가를 분리한다. "
                        "출력은 JSON만 반환한다."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"task": "instant_symbol_analysis", "context": context}, ensure_ascii=False),
                },
            ],
            "context": context,
            "response_schema": {
                "summary": "쥬의 한 줄 이상 종합 평가",
                "stance": "watch|confirm|hold|risk_check|avoid|block_candidate",
                "confidence": 0.0,
                "short_view": "단기 평가",
                "mid_view": "중기 평가",
                "long_view": "장기 평가",
                "reasons": ["근거"],
                "risks": ["반론"],
                "data_gaps": ["부족한 자료"],
                "triggers": ["다음 확인 조건"],
                "target_candidates": [0],
                "stop_candidates": [0],
            },
            "telemetry": {"component": "symbol_analysis", "operation": "run"},
        }

    async def _call_llm(self, prompt: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if not getattr(self.codex_runtime, "ready", False):
            return self._analysis_unavailable("codex_runtime_unavailable"), {"ok": False, "error": "codex_runtime_unavailable"}
        raw = await self.codex_runtime.complete(prompt, timeout_ms=self.timeout_ms)
        parsed = _parse_json_content(raw) if raw.get("ok") else {}
        if not parsed:
            return self._analysis_unavailable(str(raw.get("error") or "empty_llm_response")), raw
        return {
            "summary": str(parsed.get("summary") or "")[:2000],
            "stance": str(parsed.get("stance") or "watch")[:80],
            "confidence": float(parsed.get("confidence") or 0),
            "short_view": str(parsed.get("short_view") or "")[:1200],
            "mid_view": str(parsed.get("mid_view") or "")[:1200],
            "long_view": str(parsed.get("long_view") or "")[:1200],
            "reasons": _string_list(parsed.get("reasons")),
            "risks": _string_list(parsed.get("risks")),
            "data_gaps": _string_list(parsed.get("data_gaps")),
            "triggers": _string_list(parsed.get("triggers")),
            "target_candidates": list(parsed.get("target_candidates") or [])[:8],
            "stop_candidates": list(parsed.get("stop_candidates") or [])[:8],
        }, raw

    @staticmethod
    def _analysis_unavailable(reason: str) -> dict[str, Any]:
        return {
            "summary": "쥬 정성 분석 생성 실패. 데이터 snapshot만 저장한다.",
            "stance": "stale",
            "confidence": 0.0,
            "short_view": "",
            "mid_view": "",
            "long_view": "",
            "reasons": [],
            "risks": [reason],
            "data_gaps": ["llm_analysis_unavailable"],
            "triggers": [],
            "target_candidates": [],
            "stop_candidates": [],
            "status": "error",
            "error_message": reason,
        }
```

- [ ] **Step 4: Run service test and verify pass**

Run:

```bash
pytest tests/test_symbol_analysis.py::test_run_instant_analysis_collects_fundamentals_and_persists -q
```

Expected: pass.

---

### Task 3: Add API And Telegram Entry Points

**Files:**
- Modify: `src/tradecraft/main.py`
- Modify: `tests/test_api_smoke.py`
- Modify: `tests/test_telegram_cli.py`

- [ ] **Step 1: Write API tests**

Append to `tests/test_api_smoke.py`:

```python
def test_symbol_analysis_api_routes(monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from tradecraft import main

    class FakeSymbolAnalysis:
        async def run(self, symbol, *, trigger="user_request", force_collect=True):
            return {
                "status": "ok",
                "symbol": symbol,
                "name": "피노",
                "analysis": {"summary": "피노 분석", "stance": "risk_check"},
            }

        def history(self, symbol, *, limit=10):
            return {"status": "ok", "symbol": symbol, "items": [{"summary": "피노 분석"}]}

        def special_watch(self):
            return {"status": "ok", "items": [{"symbol": "033790", "name": "피노"}]}

    monkeypatch.setenv("TRADECRAFT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(main, "symbol_analysis_service", FakeSymbolAnalysis(), raising=False)
    client = TestClient(main.app)

    headers = {"Authorization": "Bearer test-token"}
    run = client.post("/api/symbols/033790/analysis/run", headers=headers)
    history = client.get("/api/symbols/033790/analysis/history", headers=headers)
    special = client.get("/api/symbols/special-watch", headers=headers)

    assert run.status_code == 200
    assert run.json()["analysis"]["stance"] == "risk_check"
    assert history.status_code == 200
    assert history.json()["items"][0]["summary"] == "피노 분석"
    assert special.status_code == 200
    assert special.json()["items"][0]["symbol"] == "033790"
```

- [ ] **Step 2: Run API test and verify failure**

Run:

```bash
pytest tests/test_api_smoke.py::test_symbol_analysis_api_routes -q
```

Expected: failure because routes and `symbol_analysis_service` are missing.

- [ ] **Step 3: Wire service in `main.py`**

Import the service:

```python
from tradecraft.services.symbol_analysis import SymbolAnalysisService
```

After existing service construction, add:

```python
symbol_analysis_service = SymbolAnalysisService(
    codex_runtime=codex_runtime,
    memory_service=investment_memory_service,
    fundamentals=symbol_fundamentals_service,
    quote_provider=market_judgment_engine.quote_service,
    report_repository=report_stack.repository,
    rag_store=report_stack.rag_store,
    block_provider=kis_block_trader,
    timeout_ms=settings.codex_runtime_timeout_ms,
)
```

If local variable names differ in `main.py`, use the already-instantiated objects serving the same roles: `bridge`/`codex_runtime`, `stack.repository`, `stack.rag_store`, `market_judgment_engine`, and `kis_block_trader`.

- [ ] **Step 4: Add admin-gated API routes**

Add near existing symbol fundamentals routes:

```python
@app.post("/api/symbols/{symbol}/analysis/run")
async def symbol_analysis_run(
    symbol: str,
    payload: dict[str, Any] | None = None,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    body = payload or {}
    force_collect = bool(body.get("force_collect", True)) if isinstance(body, dict) else True
    trigger = str(body.get("trigger") or "user_request") if isinstance(body, dict) else "user_request"
    return await symbol_analysis_service.run(
        symbol,
        trigger=trigger,
        force_collect=force_collect,
    )


@app.get("/api/symbols/{symbol}/analysis/history")
async def symbol_analysis_history(
    symbol: str,
    limit: int = 10,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    return symbol_analysis_service.history(symbol, limit=max(min(int(limit), 50), 1))


@app.get("/api/symbols/special-watch")
async def symbol_special_watch(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    return symbol_analysis_service.special_watch()
```

- [ ] **Step 5: Add missing service helpers**

Add to `SymbolAnalysisService`:

```python
    def history(self, symbol: str, *, limit: int = 10) -> dict[str, Any]:
        return self.memory_service.repository.list_symbol_analyses(symbol, limit=limit)

    def special_watch(self) -> dict[str, Any]:
        payload = self.block_provider.blocks() if self.block_provider else {}
        blocks = payload.get("blocks") if isinstance(payload, dict) else []
        rows = []
        for row in list(blocks or []):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if row.get("created_by") == "existing_position" or metadata.get("adopted_from_account"):
                rows.append(
                    {
                        "symbol": str(row.get("symbol") or ""),
                        "name": str(row.get("name") or ""),
                        "block_id": str(row.get("block_id") or ""),
                        "status": str(row.get("status") or ""),
                        "reason": "existing_position",
                    }
                )
        return {"status": "ok", "count": len(rows), "items": rows}
```

- [ ] **Step 6: Add Telegram command tests**

Extend the existing Telegram command test fixture so `/analyze 033790` calls `symbol_analysis_service.run()` and returns the summary. Add a test shaped like:

```python
def test_telegram_analyze_symbol_command(monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from tradecraft import main

    class FakeSymbolAnalysis:
        async def run(self, symbol, *, trigger="telegram", force_collect=True):
            assert symbol == "033790"
            return {
                "status": "ok",
                "symbol": "033790",
                "name": "피노",
                "analysis": {
                    "summary": "피노는 단기 방어 블록만 허용",
                    "stance": "risk_check",
                    "confidence": 0.72,
                    "risks": ["밸류 공백"],
                },
            }

    monkeypatch.setattr(main, "symbol_analysis_service", FakeSymbolAnalysis(), raising=False)
    monkeypatch.setenv("TRADECRAFT_TELEGRAM_CHAT_ID", "123")
    client = TestClient(main.app)

    response = client.post(
        "/api/telegram/webhook",
        json={"message": {"chat": {"id": 123}, "text": "/analyze 033790"}},
    )

    assert response.status_code == 200
    assert response.json()["handled"] is True
```

- [ ] **Step 7: Implement Telegram branch**

In the Telegram webhook command dispatch in `main.py`, add:

```python
    elif command == "analyze":
        symbol = args[0] if args else ""
        payload = await symbol_analysis_service.run(
            symbol,
            trigger="telegram",
            force_collect=True,
        )
        analysis = payload.get("analysis") if isinstance(payload, dict) else {}
        handled = True
        reply = (
            f"쥬 종목분석 {payload.get('name') or symbol}({payload.get('symbol') or symbol})\n"
            f"{analysis.get('stance') or '-'} · confidence {float(analysis.get('confidence') or 0):.2f}\n"
            f"{analysis.get('summary') or '-'}"
        )
```

- [ ] **Step 8: Run route tests**

Run:

```bash
pytest tests/test_api_smoke.py::test_symbol_analysis_api_routes tests/test_telegram_cli.py::test_telegram_analyze_symbol_command -q
```

Expected: pass.

---

### Task 4: Inject Recent Symbol Analyses Into 쥬 Decisions

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/services/market_judgment.py`
- Test: `tests/test_investment_memory.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Write context-pack test**

Append to `tests/test_investment_memory.py`:

```python
def test_context_pack_includes_recent_symbol_analyses(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()
    service.repository.save_symbol_analysis(
        {
            "symbol": "033790",
            "name": "피노",
            "trigger": "user_request",
            "source": "instant",
            "model": "gpt-5.5",
            "status": "ok",
            "summary": "피노는 짧은 단기 블록만 허용한다.",
            "stance": "risk_check",
            "confidence": 0.7,
        }
    )

    pack = service.context_pack(symbols=["033790"], max_chars=4000)

    assert "symbol_analyses" in pack
    assert pack["symbol_analyses"]["033790"][0]["summary"] == "피노는 짧은 단기 블록만 허용한다."
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
pytest tests/test_investment_memory.py::test_context_pack_includes_recent_symbol_analyses -q
```

Expected: failure because `context_pack()` does not include `symbol_analyses`.

- [ ] **Step 3: Add context-pack injection**

Inside `InvestmentMemoryService.context_pack()`, after symbol memories are loaded, add:

```python
        symbol_analyses: dict[str, list[dict[str, Any]]] = {}
        for symbol in normalized_symbols:
            history = self.repository.list_symbol_analyses(symbol, limit=3)
            items = history.get("items") if isinstance(history, dict) else []
            if items:
                symbol_analyses[symbol] = [
                    {
                        "created_at": row.get("created_at"),
                        "trigger": row.get("trigger"),
                        "stance": row.get("stance"),
                        "confidence": row.get("confidence"),
                        "summary": _clean_text(row.get("summary"), limit=500),
                        "risks": list(row.get("risks") or [])[:3],
                        "data_gaps": list(row.get("data_gaps") or [])[:3],
                    }
                    for row in list(items)[:3]
                    if isinstance(row, dict)
                ]
        if symbol_analyses:
            payload["symbol_analyses"] = symbol_analyses
```

Use the local symbol list variable already present in `context_pack()`. If it is named differently, normalize the existing `symbols` argument once with `_is_symbol()`.

- [ ] **Step 4: Verify block manager receives analyses**

Add to an existing prompt/context test in `tests/test_kis_block_trader.py`:

```python
assert "symbol_analyses" in prompt["investment_memory"]
```

Set the fake memory provider in that test to return:

```python
{
    "status": "ok",
    "symbol_analyses": {
        "033790": [
            {
                "summary": "피노는 과거 근거 공백 때문에 짧은 블록만 허용",
                "stance": "risk_check",
            }
        ]
    },
}
```

- [ ] **Step 5: Keep provider call symbol-aware**

In `KISBlockTrader._investment_memory_context()` and `MarketJudgmentEngine._investment_memory_context()`, ensure calls pass focus symbols:

```python
return provider(
    symbols=symbols,
    block_ids=block_ids,
    market_judgment=market_judgment,
    max_chars=self.config.memory_context_max_chars,
)
```

If the provider currently accepts `**kwargs`, preserve existing arguments and only add `symbols=symbols`.

- [ ] **Step 6: Run tests**

Run:

```bash
pytest tests/test_investment_memory.py::test_context_pack_includes_recent_symbol_analyses tests/test_kis_block_trader.py -q
```

Expected: pass.

---

### Task 5: Auto-Trigger Special Watch Analysis For User-Owned Or Adopted Holdings

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/services/symbol_analysis.py`
- Test: `tests/test_kis_block_trader.py`
- Test: `tests/test_symbol_analysis.py`

- [ ] **Step 1: Write special-watch detection test**

Add to `tests/test_symbol_analysis.py`:

```python
def test_special_watch_extracts_existing_position_blocks(tmp_path: Path) -> None:
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(tmp_path / "strategy.md"),
        ),
        codex_runtime=None,  # type: ignore[arg-type]
    )
    memory.initialize()
    service = SymbolAnalysisService(
        codex_runtime=_FakeLLM(),  # type: ignore[arg-type]
        memory_service=memory,
        fundamentals=_FakeFundamentals(),  # type: ignore[arg-type]
        quote_provider=_FakeQuoteProvider(),  # type: ignore[arg-type]
        report_repository=_FakeReports(),  # type: ignore[arg-type]
        rag_store=_FakeRAG(),  # type: ignore[arg-type]
        block_provider=_FakeBlocks(),  # type: ignore[arg-type]
    )

    payload = service.special_watch()

    assert payload["status"] == "ok"
    assert payload["items"][0]["symbol"] == "033790"
```

- [ ] **Step 2: Run test and verify pass after Task 3 helper**

Run:

```bash
pytest tests/test_symbol_analysis.py::test_special_watch_extracts_existing_position_blocks -q
```

Expected: pass if Task 3 helper is implemented correctly.

- [ ] **Step 3: Define non-blocking trigger interface**

Add an optional `symbol_analysis_runner` callback to `KISBlockTrader.__init__`:

```python
        symbol_analysis_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
```

Store it:

```python
        self.symbol_analysis_runner = symbol_analysis_runner
```

- [ ] **Step 4: Trigger after adopted existing blocks**

After successful `adopt_existing_blocks` handling, collect adopted symbols and call:

```python
        if self.symbol_analysis_runner and adopted_symbols:
            for symbol in sorted(set(adopted_symbols)):
                try:
                    await self.symbol_analysis_runner(
                        symbol,
                        trigger="existing_position_adopted",
                        force_collect=True,
                    )
                except Exception as exc:
                    self.repository.add_event(
                        block_id=f"symbol:{symbol}",
                        event_type="symbol_analysis_failed",
                        message=str(exc)[:300],
                        payload={"symbol": symbol},
                    )
```

The trigger must not block order reconciliation or rule execution. Failures are events, not trading halts.

- [ ] **Step 5: Wire callback in runners/main**

Where `KISBlockTrader` is instantiated, pass:

```python
symbol_analysis_runner=symbol_analysis_service.run,
```

If construction order makes this circular, pass no callback in `main.py` first and wire it in the runtime runner where service instances can be ordered safely. The API remains useful even before auto-trigger is wired.

- [ ] **Step 6: Run KIS block tests**

Run:

```bash
pytest tests/test_kis_block_trader.py tests/test_symbol_analysis.py -q
```

Expected: pass.

---

### Task 6: Add UI For Instant Analysis And History

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/index.html`
- Test: `node --check src/tradecraft/web/static/app.js`

- [ ] **Step 1: Add frontend state**

In the global `state` object in `app.js`, add:

```javascript
symbolAnalysis: {
  symbol: "",
  running: false,
  result: null,
  history: null,
  error: "",
},
```

- [ ] **Step 2: Add API helpers**

Add functions:

```javascript
async function runSymbolAnalysis(symbol) {
  state.symbolAnalysis.running = true;
  state.symbolAnalysis.error = "";
  render();
  try {
    const payload = await getJSON(`/symbols/${encodeURIComponent(symbol)}/analysis/run`, {
      method: "POST",
      body: JSON.stringify({ trigger: "ui", force_collect: true }),
    });
    state.symbolAnalysis.result = payload;
    state.symbolAnalysis.history = await getJSON(`/symbols/${encodeURIComponent(symbol)}/analysis/history?limit=8`);
  } catch (error) {
    state.symbolAnalysis.error = error instanceof Error ? error.message : String(error);
  } finally {
    state.symbolAnalysis.running = false;
    render();
  }
}
```

Use the existing central fetch helper and token-aware headers.

- [ ] **Step 3: Add render panel**

In the investment helper/strategy area, add a panel:

```javascript
function renderSymbolAnalysisPanel() {
  const result = state.symbolAnalysis.result || {};
  const analysis = result.analysis || {};
  const history = state.symbolAnalysis.history?.items || [];
  return `
    <section class="research-panel symbol-analysis-panel">
      <div class="panel-head">
        <div>
          <span class="eyebrow">Instant Symbol Analysis</span>
          <h3>쥬 즉시 종목분석</h3>
        </div>
        <div class="inline-form">
          <input id="symbol-analysis-input" value="${escapeHTML(state.symbolAnalysis.symbol || "")}" placeholder="종목코드 또는 종목명" />
          <button class="btn primary" type="button" data-symbol-analysis-run ${state.symbolAnalysis.running ? "disabled" : ""}>
            ${state.symbolAnalysis.running ? "분석 중" : "분석"}
          </button>
        </div>
      </div>
      ${state.symbolAnalysis.error ? `<p class="status bad">${escapeHTML(state.symbolAnalysis.error)}</p>` : ""}
      ${analysis.summary ? `
        <article class="analysis-result-card">
          <div class="analysis-result-head">
            <strong>${escapeHTML(result.name || result.symbol || "-")}</strong>
            <span>${escapeHTML(analysis.stance || "-")} · ${escapeHTML(String(analysis.confidence || 0))}</span>
          </div>
          <p>${escapeHTML(analysis.summary || "")}</p>
          <div class="horizon-grid">
            <div><b>단기</b><span>${escapeHTML(analysis.short_view || "-")}</span></div>
            <div><b>중기</b><span>${escapeHTML(analysis.mid_view || "-")}</span></div>
            <div><b>장기</b><span>${escapeHTML(analysis.long_view || "-")}</span></div>
          </div>
        </article>
      ` : ""}
      <div class="analysis-history">
        ${history.map((row) => `
          <article class="history-row">
            <span>${escapeHTML(fmtKST(row.created_at, true))}</span>
            <strong>${escapeHTML(row.stance || "-")}</strong>
            <p>${escapeHTML(row.summary || "")}</p>
          </article>
        `).join("")}
      </div>
    </section>
  `;
}
```

- [ ] **Step 4: Wire DOM events**

In the global click/input handlers:

```javascript
const symbolAnalysisButton = target ? target.closest("[data-symbol-analysis-run]") : null;
if (symbolAnalysisButton) {
  const input = document.getElementById("symbol-analysis-input");
  const symbol = input ? input.value.trim() : state.symbolAnalysis.symbol;
  state.symbolAnalysis.symbol = symbol;
  runSymbolAnalysis(symbol);
  return;
}
```

- [ ] **Step 5: Cache-bust index**

Update script/style query version in `index.html` to:

```html
?v=20260519_symbol_analysis_memory_v1
```

- [ ] **Step 6: Static JS check**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no syntax errors.

---

### Task 7: Final Verification And Regression

**Files:**
- No new files; verify all touched files.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
pytest tests/test_symbol_analysis.py tests/test_investment_memory.py tests/test_api_smoke.py tests/test_kis_block_trader.py tests/test_telegram_cli.py -q
```

Expected: pass.

- [ ] **Step 2: Run static frontend check**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run diff whitespace check**

Run:

```bash
git diff --check -- src/tradecraft/services/symbol_analysis.py src/tradecraft/services/investment_memory.py src/tradecraft/main.py src/tradecraft/services/kis_block_trader.py src/tradecraft/services/market_judgment.py src/tradecraft/web/static/app.js src/tradecraft/web/static/index.html tests/test_symbol_analysis.py tests/test_investment_memory.py tests/test_api_smoke.py tests/test_kis_block_trader.py tests/test_telegram_cli.py
```

Expected: no output and exit code 0.

- [ ] **Step 4: Manual runtime smoke**

With the local control app running and an admin token configured, run:

```bash
curl -sS -X POST http://127.0.0.1:18080/api/symbols/033790/analysis/run \
  -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"trigger":"manual_smoke","force_collect":true}' | python3 -m json.tool
```

Expected:

```json
{
  "status": "ok",
  "symbol": "033790",
  "name": "피노",
  "analysis": {
    "summary": "...",
    "stance": "...",
    "confidence": 0.0
  }
}
```

- [ ] **Step 5: Confirm history reuse**

Run:

```bash
sqlite3 -header -column .runtime/investment_memory.db \
  "SELECT symbol,name,trigger,stance,confidence,substr(summary,1,80) AS summary,created_at FROM symbol_analyses WHERE symbol='033790' ORDER BY created_at DESC LIMIT 5;"
```

Expected: at least one row for `033790` with the manual smoke trigger and 쥬 summary.

---

## Self-Review

- Spec coverage: The plan covers instant analysis, qualitative 쥬 judgment, per-symbol DB history, Markdown memory mirroring, special-watch handling, API, Telegram, UI, and decision-context reuse.
- Placeholder scan: No TODO/TBD placeholders are used. Each implementation task includes concrete files, test commands, and code shape.
- Type consistency: `symbol_analyses`, `SymbolAnalysisService.run()`, `history()`, `special_watch()`, and `context_pack(symbols=...)` are named consistently across tasks.
- Scope check: This is a single cohesive feature. ETF-specific expansion, advanced charting, and order-sizing changes are intentionally out of scope.
