# Jue Daily Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Jue's pre-open random deep research loop: each trading morning, sample 5 KOSPI and 5 KOSDAQ stocks, run deep gpt-5.5-backed symbol analysis for all 10, persist the discoveries, and feed strong candidates into Jue's memory and block manager.

**Architecture:** Add a focused `daily_discovery` service with its own SQLite DB for run/sample metadata while reusing existing `SymbolAnalysisService`, `SymbolFundamentalsService`, `InvestmentMemoryService`, and `KISBlockTrader`. Sampling comes from the existing Naver report `symbol_directory`, with freshness and exclusion rules so discovery expands the universe instead of repeating recent analyses. Discovery never sends orders directly; it produces ranked study results and block-candidate context that the existing gated block manager may use.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, pytest, existing static JS/CSS frontend, existing CodexNativeRuntime usage accounting.

---

## File Structure

- Create `src/tradecraft/services/daily_discovery.py`
  - Owns `DailyDiscoveryConfig`, `DailyDiscoveryRepository`, and `DailyDiscoveryService`.
  - Samples KOSPI/KOSDAQ symbols, records idempotent daily runs, calls `SymbolAnalysisService.run()` for every selected symbol, ranks outcomes, and exposes compact latest context.
- Modify `src/tradecraft/services/naver_reports.py`
  - Add a public symbol-directory listing method used by discovery sampling.
- Modify `src/tradecraft/config.py`
  - Add discovery env knobs with conservative defaults.
- Modify `src/tradecraft/main.py`
  - Instantiate discovery service and add admin-protected API endpoints.
  - Feed discovery context into `KISBlockTrader`.
- Modify `src/tradecraft/runtime/investment_memory_runner.py`
  - Run discovery once during the pre-open window before the pre-open ritual message.
- Modify `src/tradecraft/runtime/kis_block_trader_runner.py`
  - Build discovery context provider for the block manager runner.
- Modify `src/tradecraft/services/kis_block_trader.py`
  - Add optional discovery context provider and include compact daily discoveries in manager prompts.
- Modify `src/tradecraft/services/investment_memory.py`
  - Include compact daily discovery summaries in context packs and ritual contexts.
- Modify `src/tradecraft/web/static/app.js` and `src/tradecraft/web/static/style.css`
  - Show "쥬 아침 탐사" status, sampled symbols, deep-analysis results, and block-candidate chips.
- Create `tests/test_daily_discovery.py`
  - Unit tests for sampling, idempotency, full-depth analysis, ranking, and compact context.
- Modify `tests/test_kis_block_trader.py`
  - Verify discovery context reaches the block manager prompt.
- Modify `tests/test_investment_memory.py`
  - Verify discovery context is compacted into memory.
- Modify `tests/test_api_smoke.py` or create focused API tests in `tests/test_daily_discovery_api.py`
  - Verify endpoints and admin gating.

No git commit is part of this implementation unless the user explicitly asks for a commit. Each task ends with a diff review/checkpoint instead.

---

## Task 1: Symbol Directory Sampling Source

**Files:**
- Modify: `src/tradecraft/services/naver_reports.py`
- Test: `tests/test_daily_discovery.py`

- [x] **Step 1: Write failing tests for KOSPI/KOSDAQ symbol listing**

Create `tests/test_daily_discovery.py` with these initial tests:

```python
from __future__ import annotations

from pathlib import Path

from tradecraft.services.naver_reports import NaverReportRepository


def test_symbol_directory_lists_market_symbols_for_discovery(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [
            {"symbol": "005930", "name": "삼성전자", "market": "KOSPI", "source": "test"},
            {"symbol": "000660", "name": "SK하이닉스", "market": "KOSPI", "source": "test"},
            {"symbol": "035720", "name": "카카오", "market": "KOSPI", "source": "test"},
            {"symbol": "091990", "name": "셀트리온헬스케어", "market": "KOSDAQ", "source": "test"},
            {"symbol": "277810", "name": "레인보우로보틱스", "market": "KOSDAQ", "source": "test"},
            {"symbol": "069500", "name": "KODEX 200", "market": "ETF", "source": "test"},
        ]
    )

    kospi = repo.list_symbol_directory(market="KOSPI", limit=10)
    kosdaq = repo.list_symbol_directory(market="KOSDAQ", limit=10)

    assert [row["symbol"] for row in kospi] == ["000660", "005930", "035720"]
    assert [row["symbol"] for row in kosdaq] == ["091990", "277810"]
    assert all(row["asset_class"] == "stock" for row in kospi + kosdaq)


def test_symbol_directory_listing_excludes_symbols_and_non_stock_assets(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [
            {"symbol": "005930", "name": "삼성전자", "market": "KOSPI", "source": "test"},
            {"symbol": "000660", "name": "SK하이닉스", "market": "KOSPI", "source": "test"},
            {"symbol": "069500", "name": "KODEX 200", "market": "ETF", "source": "test"},
        ]
    )

    rows = repo.list_symbol_directory(
        market="KOSPI",
        limit=10,
        exclude_symbols={"005930"},
    )

    assert [row["symbol"] for row in rows] == ["000660"]
```

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_daily_discovery.py::test_symbol_directory_lists_market_symbols_for_discovery -q
```

Expected: fail with `AttributeError: 'NaverReportRepository' object has no attribute 'list_symbol_directory'`.

- [x] **Step 3: Implement `list_symbol_directory`**

Add this method inside `NaverReportRepository` near `resolve_symbol_names`:

```python
    def list_symbol_directory(
        self,
        *,
        market: str = "",
        limit: int = 100,
        exclude_symbols: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        market_filter = str(market or "").strip().upper()
        excluded = {str(item or "").strip() for item in (exclude_symbols or set())}
        params: list[Any] = []
        where = [
            "TRIM(symbol) <> ''",
            "TRIM(company_name) <> ''",
            "market NOT IN ('ETF', 'ETN')",
            "status NOT IN ('halted', 'managed', 'delisted')",
        ]
        if market_filter:
            where.append("UPPER(market) = ?")
            params.append(market_filter)
        query = f"""
            SELECT symbol, company_name, market, source, confidence, updated_at
            FROM symbol_directory
            WHERE {' AND '.join(where)}
            ORDER BY symbol ASC
            LIMIT ?
        """
        params.append(max(int(limit), 1) + len(excluded))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            symbol = str(row["symbol"] or "").strip()
            if symbol in excluded:
                continue
            out.append(
                {
                    "symbol": symbol,
                    "name": str(row["company_name"] or ""),
                    "market": str(row["market"] or ""),
                    "source": str(row["source"] or ""),
                    "confidence": float(row["confidence"] or 0),
                    "updated_at": str(row["updated_at"] or ""),
                    "asset_class": "stock",
                }
            )
            if len(out) >= max(int(limit), 1):
                break
        return out
```

- [x] **Step 4: Run Task 1 tests**

Run:

```bash
pytest tests/test_daily_discovery.py -q
```

Expected: `2 passed`.

- [x] **Step 5: Review diff**

Run:

```bash
git diff -- src/tradecraft/services/naver_reports.py tests/test_daily_discovery.py
```

Expected: only the new repository method and tests are shown.

---

## Task 2: Daily Discovery Repository and Sampling

**Files:**
- Create: `src/tradecraft/services/daily_discovery.py`
- Test: `tests/test_daily_discovery.py`

- [x] **Step 1: Add failing tests for daily sampling and idempotency**

Append:

```python
from datetime import date

from tradecraft.services.daily_discovery import (
    DailyDiscoveryConfig,
    DailyDiscoveryRepository,
    DailyDiscoveryService,
)


class _DirectorySource:
    def __init__(self) -> None:
        self.rows = {
            "KOSPI": [
                {"symbol": f"10{idx:04d}", "name": f"피코스피{idx}", "market": "KOSPI"}
                for idx in range(20)
            ],
            "KOSDAQ": [
                {"symbol": f"20{idx:04d}", "name": f"피코스닥{idx}", "market": "KOSDAQ"}
                for idx in range(20)
            ],
        }

    def list_symbol_directory(self, *, market: str = "", limit: int = 100, exclude_symbols=None):
        excluded = set(exclude_symbols or set())
        return [
            row for row in self.rows[str(market).upper()]
            if row["symbol"] not in excluded
        ][:limit]


def test_daily_discovery_samples_five_kospi_and_five_kosdaq_deterministically(tmp_path: Path) -> None:
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=str(tmp_path / "discovery.db")),
        directory_source=_DirectorySource(),
        symbol_analysis=None,
    )

    first = service.select_symbols(trading_day=date(2026, 5, 20))
    second = service.select_symbols(trading_day=date(2026, 5, 20))

    assert first == second
    assert len([row for row in first if row["market"] == "KOSPI"]) == 5
    assert len([row for row in first if row["market"] == "KOSDAQ"]) == 5
    assert len({row["symbol"] for row in first}) == 10


def test_daily_discovery_skips_recently_analyzed_symbols(tmp_path: Path) -> None:
    repo = DailyDiscoveryRepository(str(tmp_path / "discovery.db"))
    repo.save_run(
        {
            "trading_day": "2026-05-19",
            "status": "ok",
            "selected_symbols": ["100000", "200000"],
            "results": [],
        }
    )
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(
            db_path=str(tmp_path / "discovery.db"),
            exclude_recent_days=5,
        ),
        directory_source=_DirectorySource(),
        symbol_analysis=None,
    )

    selected = service.select_symbols(trading_day=date(2026, 5, 20))
    symbols = {row["symbol"] for row in selected}

    assert "100000" not in symbols
    assert "200000" not in symbols
```

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_daily_discovery.py::test_daily_discovery_samples_five_kospi_and_five_kosdaq_deterministically -q
```

Expected: fail with `ModuleNotFoundError: No module named 'tradecraft.services.daily_discovery'`.

- [x] **Step 3: Create repository and sampling implementation**

Create `src/tradecraft/services/daily_discovery.py`:

```python
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


KST_DATE_FORMAT = "%Y-%m-%d"
DISCOVERY_MARKETS = ("KOSPI", "KOSDAQ")


class SymbolDirectorySource(Protocol):
    def list_symbol_directory(
        self,
        *,
        market: str = "",
        limit: int = 100,
        exclude_symbols: set[str] | None = None,
    ) -> list[dict[str, Any]]: ...


class SymbolAnalysisRunner(Protocol):
    async def run(
        self,
        symbol_or_name: str,
        *,
        trigger: str = "user_request",
        force_collect: bool = True,
    ) -> dict[str, Any]: ...


@dataclass
class DailyDiscoveryConfig:
    db_path: str = ".runtime/jue_daily_discovery.db"
    enabled: bool = True
    kospi_count: int = 5
    kosdaq_count: int = 5
    exclude_recent_days: int = 10
    candidate_limit_per_market: int = 300
    force_collect: bool = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


def _stable_sample(rows: list[dict[str, Any]], *, seed: str, count: int) -> list[dict[str, Any]]:
    decorated: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        digest = hashlib.sha256(f"{seed}:{symbol}".encode("utf-8")).hexdigest()
        score = int(digest[:16], 16)
        decorated.append((score, row))
    decorated.sort(key=lambda item: item[0])
    return [dict(row) for _, row in decorated[: max(int(count), 0)]]


class DailyDiscoveryRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS discovery_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trading_day TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    selected_symbols_json TEXT NOT NULL DEFAULT '[]',
                    results_json TEXT NOT NULL DEFAULT '[]',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS discovery_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trading_day TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    market TEXT NOT NULL DEFAULT '',
                    rank INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'selected',
                    analysis_id INTEGER,
                    stance TEXT NOT NULL DEFAULT '',
                    confidence REAL,
                    score REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(trading_day, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_discovery_samples_symbol
                    ON discovery_samples(symbol, trading_day DESC);
                """
            )

    def recent_symbols(self, *, before_day: date, days: int) -> set[str]:
        cutoff = (before_day - timedelta(days=max(int(days), 0))).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT symbol
                FROM discovery_samples
                WHERE trading_day >= ? AND trading_day < ?
                """,
                (cutoff, before_day.isoformat()),
            ).fetchall()
        return {str(row["symbol"] or "") for row in rows if row["symbol"]}

    def latest_run(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM discovery_runs ORDER BY trading_day DESC, id DESC LIMIT 1"
            ).fetchone()
        return self._row_to_run(row) if row else {"status": "missing"}

    def run_for_day(self, trading_day: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM discovery_runs WHERE trading_day = ? LIMIT 1",
                (trading_day,),
            ).fetchone()
        return self._row_to_run(row) if row else None

    def save_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = _now_iso()
        trading_day = str(payload.get("trading_day") or "")
        selected = list(payload.get("selected_symbols") or [])
        results = list(payload.get("results") or [])
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO discovery_runs (
                    trading_day, status, selected_symbols_json, results_json,
                    summary_json, error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trading_day) DO UPDATE SET
                    status=excluded.status,
                    selected_symbols_json=excluded.selected_symbols_json,
                    results_json=excluded.results_json,
                    summary_json=excluded.summary_json,
                    error_message=excluded.error_message,
                    updated_at=excluded.updated_at
                """,
                (
                    trading_day,
                    str(payload.get("status") or "ok"),
                    _json_dumps(selected),
                    _json_dumps(results),
                    _json_dumps(summary),
                    str(payload.get("error_message") or "")[:500],
                    now,
                    now,
                ),
            )
            for rank, row in enumerate(results, start=1):
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").strip()
                if not symbol:
                    continue
                analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
                conn.execute(
                    """
                    INSERT INTO discovery_samples (
                        trading_day, symbol, name, market, rank, status,
                        analysis_id, stance, confidence, score, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trading_day, symbol) DO UPDATE SET
                        name=excluded.name,
                        market=excluded.market,
                        rank=excluded.rank,
                        status=excluded.status,
                        analysis_id=excluded.analysis_id,
                        stance=excluded.stance,
                        confidence=excluded.confidence,
                        score=excluded.score,
                        updated_at=excluded.updated_at
                    """,
                    (
                        trading_day,
                        symbol,
                        str(row.get("name") or ""),
                        str(row.get("market") or ""),
                        rank,
                        str(row.get("status") or "ok"),
                        analysis.get("id"),
                        str(analysis.get("stance") or row.get("stance") or ""),
                        analysis.get("confidence") or row.get("confidence"),
                        row.get("score"),
                        now,
                        now,
                    ),
                )
        return self.run_for_day(trading_day) or {"status": "missing"}

    def _row_to_run(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"] or 0),
            "trading_day": str(row["trading_day"] or ""),
            "status": str(row["status"] or ""),
            "selected_symbols": _json_loads(row["selected_symbols_json"], []),
            "results": _json_loads(row["results_json"], []),
            "summary": _json_loads(row["summary_json"], {}),
            "error_message": str(row["error_message"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }


class DailyDiscoveryService:
    def __init__(
        self,
        *,
        config: DailyDiscoveryConfig,
        directory_source: SymbolDirectorySource,
        symbol_analysis: SymbolAnalysisRunner | None,
    ) -> None:
        self.config = config
        self.repository = DailyDiscoveryRepository(config.db_path)
        self.directory_source = directory_source
        self.symbol_analysis = symbol_analysis

    def select_symbols(self, *, trading_day: date) -> list[dict[str, Any]]:
        excluded = self.repository.recent_symbols(
            before_day=trading_day,
            days=self.config.exclude_recent_days,
        )
        selected: list[dict[str, Any]] = []
        counts = {"KOSPI": self.config.kospi_count, "KOSDAQ": self.config.kosdaq_count}
        for market in DISCOVERY_MARKETS:
            rows = self.directory_source.list_symbol_directory(
                market=market,
                limit=self.config.candidate_limit_per_market,
                exclude_symbols=excluded,
            )
            sample = _stable_sample(
                rows,
                seed=f"{trading_day.isoformat()}:{market}",
                count=counts[market],
            )
            for row in sample:
                row["market"] = market
            selected.extend(sample)
        return selected
```

- [x] **Step 4: Run sampling tests**

Run:

```bash
pytest tests/test_daily_discovery.py -q
```

Expected: `4 passed`.

- [x] **Step 5: Review diff**

Run:

```bash
git diff -- src/tradecraft/services/daily_discovery.py tests/test_daily_discovery.py
```

Expected: repository/schema/sampling service plus four tests.

---

## Task 3: Deep Analysis for All 10 Symbols

**Files:**
- Modify: `src/tradecraft/services/daily_discovery.py`
- Test: `tests/test_daily_discovery.py`

- [x] **Step 1: Add failing tests for full-depth analysis**

Append:

```python
import asyncio


class _SymbolAnalysis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    async def run(self, symbol_or_name: str, *, trigger: str = "user_request", force_collect: bool = True):
        self.calls.append((symbol_or_name, trigger, force_collect))
        stance = "block_candidate" if symbol_or_name.endswith("3") else "watch"
        confidence = 0.82 if stance == "block_candidate" else 0.55
        return {
            "status": "ok",
            "symbol": symbol_or_name,
            "name": f"종목{symbol_or_name}",
            "analysis": {
                "id": len(self.calls),
                "symbol": symbol_or_name,
                "name": f"종목{symbol_or_name}",
                "stance": stance,
                "confidence": confidence,
                "summary": f"{symbol_or_name} deep study",
                "reasons": ["밸류와 수급 확인"],
                "risks": ["거래대금 확인 필요"],
            },
        }


def test_daily_discovery_runs_deep_analysis_for_every_selected_symbol(tmp_path: Path) -> None:
    analyzer = _SymbolAnalysis()
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=str(tmp_path / "discovery.db")),
        directory_source=_DirectorySource(),
        symbol_analysis=analyzer,
    )

    result = asyncio.run(service.run_once(trading_day=date(2026, 5, 20), force=True))

    assert result["status"] == "ok"
    assert result["analyzed_count"] == 10
    assert len(analyzer.calls) == 10
    assert all(call[1] == "daily_random_deep_research" for call in analyzer.calls)
    assert all(call[2] is True for call in analyzer.calls)
    assert result["summary"]["block_candidate_count"] >= 1


def test_daily_discovery_is_idempotent_for_same_trading_day(tmp_path: Path) -> None:
    analyzer = _SymbolAnalysis()
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=str(tmp_path / "discovery.db")),
        directory_source=_DirectorySource(),
        symbol_analysis=analyzer,
    )

    first = asyncio.run(service.run_once(trading_day=date(2026, 5, 20), force=False))
    second = asyncio.run(service.run_once(trading_day=date(2026, 5, 20), force=False))

    assert first["status"] == "ok"
    assert second["status"] == "skipped"
    assert second["reason"] == "already_completed"
    assert len(analyzer.calls) == 10
```

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_daily_discovery.py::test_daily_discovery_runs_deep_analysis_for_every_selected_symbol -q
```

Expected: fail with `AttributeError: 'DailyDiscoveryService' object has no attribute 'run_once'`.

- [x] **Step 3: Implement `run_once`, scoring, and latest context**

Add these functions/methods to `daily_discovery.py`:

```python
def _analysis_payload(result: dict[str, Any]) -> dict[str, Any]:
    analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
    return analysis


def _score_analysis(analysis: dict[str, Any]) -> float:
    stance = str(analysis.get("stance") or "").strip()
    confidence = float(analysis.get("confidence") or 0.0)
    base = {
        "block_candidate": 80,
        "confirm": 72,
        "hold": 62,
        "watch": 52,
        "risk_check": 42,
        "avoid": 18,
        "stale": 8,
    }.get(stance, 40)
    reasons = len(list(analysis.get("reasons") or []))
    risks = len(list(analysis.get("risks") or []))
    return round(base + confidence * 15 + min(reasons, 4) * 1.5 - min(risks, 5), 2)


def _compact_discovery_result(row: dict[str, Any]) -> dict[str, Any]:
    analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
    return {
        "symbol": str(row.get("symbol") or ""),
        "name": str(row.get("name") or analysis.get("name") or ""),
        "market": str(row.get("market") or ""),
        "status": str(row.get("status") or ""),
        "score": row.get("score"),
        "analysis": {
            "id": analysis.get("id"),
            "stance": analysis.get("stance"),
            "confidence": analysis.get("confidence"),
            "summary": str(analysis.get("summary") or "")[:500],
            "reasons": list(analysis.get("reasons") or [])[:4],
            "risks": list(analysis.get("risks") or [])[:4],
        },
    }
```

Inside `DailyDiscoveryService`, add:

```python
    async def run_once(
        self,
        *,
        trading_day: date,
        force: bool = False,
    ) -> dict[str, Any]:
        day = trading_day.isoformat()
        existing = self.repository.run_for_day(day)
        if existing and existing.get("status") == "ok" and not force:
            return {"status": "skipped", "reason": "already_completed", "run": existing}
        if self.symbol_analysis is None:
            return {"status": "error", "error_message": "symbol_analysis_missing"}

        selected = self.select_symbols(trading_day=trading_day)
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for row in selected:
            symbol = str(row.get("symbol") or "")
            try:
                result = await self.symbol_analysis.run(
                    symbol,
                    trigger="daily_random_deep_research",
                    force_collect=self.config.force_collect,
                )
            except Exception as exc:
                errors.append({"symbol": symbol, "error": str(exc)[:240]})
                results.append(
                    {
                        "symbol": symbol,
                        "name": str(row.get("name") or ""),
                        "market": str(row.get("market") or ""),
                        "status": "error",
                        "error_message": str(exc)[:240],
                        "score": 0,
                        "analysis": {"stance": "stale", "confidence": 0.0},
                    }
                )
                continue
            analysis = _analysis_payload(result if isinstance(result, dict) else {})
            merged = {
                "symbol": symbol,
                "name": str(row.get("name") or analysis.get("name") or ""),
                "market": str(row.get("market") or ""),
                "status": str(result.get("status") or "ok") if isinstance(result, dict) else "ok",
                "score": _score_analysis(analysis),
                "analysis": analysis,
            }
            results.append(merged)

        results.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        summary = {
            "selected_count": len(selected),
            "analyzed_count": len([row for row in results if row.get("status") == "ok"]),
            "error_count": len(errors),
            "block_candidate_count": len(
                [
                    row for row in results
                    if (row.get("analysis") or {}).get("stance") == "block_candidate"
                ]
            ),
            "top_symbols": [row.get("symbol") for row in results[:5]],
        }
        run = self.repository.save_run(
            {
                "trading_day": day,
                "status": "partial" if errors else "ok",
                "selected_symbols": [row.get("symbol") for row in selected],
                "results": results,
                "summary": summary,
                "error_message": "; ".join(f"{row['symbol']}:{row['error']}" for row in errors)[:500],
            }
        )
        return {
            "status": run.get("status"),
            "trading_day": day,
            "selected_count": len(selected),
            "analyzed_count": summary["analyzed_count"],
            "summary": summary,
            "results": [_compact_discovery_result(row) for row in results],
            "run": run,
        }

    def latest_context(self, *, limit: int = 10) -> dict[str, Any]:
        run = self.repository.latest_run()
        if run.get("status") == "missing":
            return {"status": "missing", "items": []}
        results = [
            _compact_discovery_result(row)
            for row in list(run.get("results") or [])[: max(int(limit), 1)]
            if isinstance(row, dict)
        ]
        return {
            "status": run.get("status"),
            "trading_day": run.get("trading_day"),
            "summary": run.get("summary") or {},
            "items": results,
            "block_candidates": [
                row for row in results
                if (row.get("analysis") or {}).get("stance") == "block_candidate"
            ],
            "updated_at": run.get("updated_at"),
        }
```

- [x] **Step 4: Run Task 3 tests**

Run:

```bash
pytest tests/test_daily_discovery.py -q
```

Expected: `6 passed`.

- [x] **Step 5: Review diff**

Run:

```bash
git diff -- src/tradecraft/services/daily_discovery.py tests/test_daily_discovery.py
```

Expected: service now executes all 10 deep analyses and persists ranked results.

---

## Task 4: Config and API Wiring

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_daily_discovery_api.py`

- [x] **Step 1: Write API tests**

Create `tests/test_daily_discovery_api.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft.main import app


def test_daily_discovery_endpoints_require_admin_token(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_ADMIN_TOKEN", "secret")
    client = TestClient(app)

    status = client.get("/api/discovery/status")
    latest = client.get("/api/discovery/latest")
    run_once = client.post("/api/discovery/run-once")

    assert status.status_code == 401
    assert latest.status_code == 401
    assert run_once.status_code == 401


def test_daily_discovery_status_shape_with_admin_token(monkeypatch) -> None:
    monkeypatch.setenv("TRADECRAFT_ADMIN_TOKEN", "secret")
    client = TestClient(app)

    response = client.get(
        "/api/discovery/status",
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "status" in payload
    assert "config" in payload
    assert "latest" in payload
```

- [x] **Step 2: Run API tests and verify failure**

Run:

```bash
pytest tests/test_daily_discovery_api.py -q
```

Expected: fail with `404 Not Found` for `/api/discovery/status`.

- [x] **Step 3: Add config fields**

In `AppSettings` near investment memory settings, add:

```python
    daily_discovery_enabled: bool = Field(
        default=True,
        alias="TRADECRAFT_DAILY_DISCOVERY_ENABLED",
    )
    daily_discovery_db_path: str = Field(
        default=".runtime/jue_daily_discovery.db",
        alias="TRADECRAFT_DAILY_DISCOVERY_DB_PATH",
    )
    daily_discovery_kospi_count: int = Field(
        default=5,
        alias="TRADECRAFT_DAILY_DISCOVERY_KOSPI_COUNT",
    )
    daily_discovery_kosdaq_count: int = Field(
        default=5,
        alias="TRADECRAFT_DAILY_DISCOVERY_KOSDAQ_COUNT",
    )
    daily_discovery_exclude_recent_days: int = Field(
        default=10,
        alias="TRADECRAFT_DAILY_DISCOVERY_EXCLUDE_RECENT_DAYS",
    )
    daily_discovery_candidate_limit_per_market: int = Field(
        default=300,
        alias="TRADECRAFT_DAILY_DISCOVERY_CANDIDATE_LIMIT_PER_MARKET",
    )
```

- [x] **Step 4: Wire service and endpoints in `main.py`**

Import:

```python
from tradecraft.services.daily_discovery import DailyDiscoveryConfig, DailyDiscoveryService
```

After `symbol_analysis_service` is created, instantiate:

```python
daily_discovery_service = DailyDiscoveryService(
    config=DailyDiscoveryConfig(
        db_path=settings.daily_discovery_db_path,
        enabled=settings.daily_discovery_enabled,
        kospi_count=settings.daily_discovery_kospi_count,
        kosdaq_count=settings.daily_discovery_kosdaq_count,
        exclude_recent_days=settings.daily_discovery_exclude_recent_days,
        candidate_limit_per_market=settings.daily_discovery_candidate_limit_per_market,
        force_collect=True,
    ),
    directory_source=naver_report_repository,
    symbol_analysis=symbol_analysis_service,
)
```

Add admin-protected endpoints:

```python
@app.get("/api/discovery/status")
async def daily_discovery_status(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    return {
        "status": "ok",
        "config": {
            "enabled": settings.daily_discovery_enabled,
            "kospi_count": settings.daily_discovery_kospi_count,
            "kosdaq_count": settings.daily_discovery_kosdaq_count,
            "exclude_recent_days": settings.daily_discovery_exclude_recent_days,
        },
        "latest": daily_discovery_service.latest_context(limit=10),
    }


@app.get("/api/discovery/latest")
async def daily_discovery_latest(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    return daily_discovery_service.latest_context(limit=10)


@app.post("/api/discovery/run-once")
async def daily_discovery_run_once(
    payload: dict[str, Any] | None = None,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    body = payload or {}
    raw_day = str(body.get("trading_day") or "").strip()
    trading_day = date.fromisoformat(raw_day) if raw_day else datetime.now(KST).date()
    force = bool(body.get("force", False))
    return await daily_discovery_service.run_once(
        trading_day=trading_day,
        force=force,
    )
```

If `date` is not imported in `main.py`, extend the datetime import:

```python
from datetime import date, datetime, time, timezone
```

- [x] **Step 5: Run API tests**

Run:

```bash
pytest tests/test_daily_discovery_api.py -q
```

Expected: `2 passed`.

- [x] **Step 6: Run config smoke**

Run:

```bash
pytest tests/test_config.py tests/test_api_smoke.py -q
```

Expected: pass.

---

## Task 5: Pre-Open Runner Integration

**Files:**
- Modify: `src/tradecraft/runtime/investment_memory_runner.py`
- Modify: `src/tradecraft/runtime/kis_block_trader_runner.py`
- Test: `tests/test_investment_memory.py`
- Test: `tests/test_kis_block_trader.py`

- [x] **Step 1: Add failing test for discovery context in memory ritual**

Append to `tests/test_investment_memory.py`:

```python
def test_context_pack_includes_daily_discovery_candidates(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.initialize()

    pack = service.context_pack(
        context={
            "daily_discovery": {
                "status": "ok",
                "trading_day": "2026-05-20",
                "summary": {"block_candidate_count": 1},
                "block_candidates": [
                    {
                        "symbol": "005930",
                        "name": "삼성전자",
                        "market": "KOSPI",
                        "score": 91,
                        "analysis": {
                            "stance": "block_candidate",
                            "confidence": 0.82,
                            "summary": "저평가와 수급 확인",
                        },
                    }
                ],
            }
        },
        max_chars=5000,
    )

    assert pack["daily_discovery"]["trading_day"] == "2026-05-20"
    assert pack["daily_discovery"]["block_candidates"][0]["symbol"] == "005930"
```

- [x] **Step 2: Run and verify failure**

Run:

```bash
pytest tests/test_investment_memory.py::test_context_pack_includes_daily_discovery_candidates -q
```

Expected: fail with `KeyError: 'daily_discovery'`.

- [x] **Step 3: Compact discovery into memory context**

In `investment_memory.py`, add helper near other compact helpers:

```python
def _compact_daily_discovery(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    items = value.get("items") if isinstance(value.get("items"), list) else []
    block_candidates = value.get("block_candidates") if isinstance(value.get("block_candidates"), list) else []
    def compact_item(row: dict[str, Any]) -> dict[str, Any]:
        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
        return {
            "symbol": str(row.get("symbol") or ""),
            "name": str(row.get("name") or ""),
            "market": str(row.get("market") or ""),
            "score": row.get("score"),
            "stance": analysis.get("stance"),
            "confidence": analysis.get("confidence"),
            "summary": _truncate(analysis.get("summary"), 240),
        }
    return {
        "status": value.get("status"),
        "trading_day": value.get("trading_day"),
        "summary": value.get("summary") if isinstance(value.get("summary"), dict) else {},
        "items": [compact_item(row) for row in items[:10] if isinstance(row, dict)],
        "block_candidates": [
            compact_item(row) for row in block_candidates[:5] if isinstance(row, dict)
        ],
    }
```

In `context_pack`, read from `context`:

```python
daily_discovery = _compact_daily_discovery(
    context_payload.get("daily_discovery")
    if isinstance(context_payload.get("daily_discovery"), dict)
    else None
)
if daily_discovery:
    payload["daily_discovery"] = daily_discovery
```

- [x] **Step 4: Add discovery build helper in runner**

In `investment_memory_runner.py`, import:

```python
from datetime import date, datetime, time, timezone
from tradecraft.services.daily_discovery import DailyDiscoveryConfig, DailyDiscoveryService
from tradecraft.services.symbol_analysis import SymbolAnalysisService
from tradecraft.services.symbol_fundamentals import SymbolFundamentalsConfig, SymbolFundamentalsService
```

Add `_build_discovery_service(settings)` using the same dependencies already used in `main.py`. The method must pass `naver_report_repository` and `SymbolAnalysisService` with `usage_component="daily_discovery"`.

In `_build_context`, after research and before usage summary:

```python
    try:
        discovery = _build_discovery_service(settings)
        context["daily_discovery"] = discovery.latest_context(limit=10)
    except Exception as exc:
        logger.warning("daily discovery context failed: %s", exc)
        context["daily_discovery"] = {"status": "error", "error_message": str(exc)}
```

In `run_investment_memory_loop`, before `run_ritual` for `pre_open`, run:

```python
            if "pre_open" in due_slots and resolved_settings.daily_discovery_enabled:
                discovery = _build_discovery_service(resolved_settings)
                discovery_result = await discovery.run_once(
                    trading_day=datetime.now(timezone.utc).astimezone(KST).date(),
                    force=False,
                )
                context["daily_discovery"] = discovery.latest_context(limit=10)
                results.append(
                    {
                        "status": discovery_result.get("status"),
                        "slot": "daily_discovery",
                        "analyzed_count": discovery_result.get("analyzed_count", 0),
                    }
                )
```

- [x] **Step 5: Run memory tests**

Run:

```bash
pytest tests/test_investment_memory.py::test_context_pack_includes_daily_discovery_candidates -q
```

Expected: pass.

- [x] **Step 6: Wire discovery into block trader runner**

In `kis_block_trader_runner.py`, build `DailyDiscoveryService` with the same DB path and pass:

```python
daily_discovery_provider=lambda: daily_discovery.latest_context(limit=10),
```

to `KISBlockTrader`.

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_prompt_includes_investment_memory_context -q
```

Expected: pass.

---

## Task 6: Block Manager Discovery Context

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_kis_block_trader.py`

- [x] **Step 1: Add failing prompt test**

Append:

```python
def test_manager_prompt_includes_daily_discovery_block_candidates(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = KISBlockTrader(
        config=KISBlockTraderConfig(
            db_path=str(tmp_path / "kis_blocks.db"),
            execute_orders=False,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        daily_discovery_provider=lambda: {
            "status": "ok",
            "trading_day": "2026-05-20",
            "block_candidates": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "score": 91,
                    "analysis": {
                        "stance": "block_candidate",
                        "confidence": 0.82,
                        "summary": "랜덤 심층 리서치에서 후보로 분류",
                    },
                }
            ],
        },
    )
    trader.clock = lambda: {"session": "pre_open", "is_market_open": False}  # type: ignore[method-assign]

    asyncio.run(trader.run_manager_once())
    prompt = json.loads(llm.calls[0]["messages"][1]["content"])

    assert prompt["daily_discovery"]["block_candidates"][0]["symbol"] == "005930"
    assert "daily_discovery" in prompt["decision_inputs"]
```

- [x] **Step 2: Run and verify failure**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_prompt_includes_daily_discovery_block_candidates -q
```

Expected: fail because `KISBlockTrader.__init__` does not accept `daily_discovery_provider`.

- [x] **Step 3: Add provider and prompt fields**

In `KISBlockTrader.__init__`, add:

```python
        daily_discovery_provider: Callable[[], dict[str, Any]] | None = None,
```

Store it:

```python
        self.daily_discovery_provider = daily_discovery_provider
```

Add helper:

```python
    def _daily_discovery_context(self) -> dict[str, Any]:
        provider = self.daily_discovery_provider
        if provider is None:
            return {"status": "missing", "items": [], "block_candidates": []}
        try:
            payload = provider()
        except Exception as exc:
            return {"status": "error", "error_message": str(exc)}
        return payload if isinstance(payload, dict) else {"status": "invalid"}
```

In `run_manager_once`, before prompt construction:

```python
        daily_discovery = self._daily_discovery_context()
```

Add to prompt:

```python
            "daily_discovery": daily_discovery,
            "decision_inputs": [
                "strategy",
                "market_judgment",
                "market_pulse",
                "investment_memory",
                "daily_discovery",
                "decision_packet_v2",
            ],
```

Also include daily discovery in memory context:

```python
            context={"daily_discovery": daily_discovery, "decision_packet_v2": decision_packet_v2},
```

If `_investment_memory_context` already uses `context`, merge this into its provider kwargs without removing existing kwargs.

- [x] **Step 4: Run KIS test**

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_prompt_includes_daily_discovery_block_candidates -q
```

Expected: pass.

- [x] **Step 5: Run nearby KIS tests**

Run:

```bash
pytest tests/test_kis_block_trader.py -q
```

Expected: pass.

---

## Task 7: UI for "쥬 아침 탐사"

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `node --check src/tradecraft/web/static/app.js`

- [x] **Step 1: Add client state and fetch**

In `state`, add:

```js
  dailyDiscovery: null,
  dailyDiscoveryError: "",
  dailyDiscoveryRunning: false,
```

Add fetch helper:

```js
async function loadDailyDiscovery() {
  try {
    state.dailyDiscovery = await apiFetch("/discovery/latest");
    state.dailyDiscoveryError = "";
  } catch (error) {
    state.dailyDiscoveryError = error.message || String(error);
  }
}
```

Add run handler:

```js
async function runDailyDiscovery() {
  state.dailyDiscoveryRunning = true;
  render();
  try {
    state.dailyDiscovery = await apiFetch("/discovery/run-once", {
      method: "POST",
      body: JSON.stringify({ force: true }),
    });
    state.dailyDiscoveryError = "";
  } catch (error) {
    state.dailyDiscoveryError = error.message || String(error);
  } finally {
    state.dailyDiscoveryRunning = false;
    render();
  }
}
```

- [x] **Step 2: Add renderer**

Add:

```js
function renderDailyDiscoveryPanel() {
  const payload = state.dailyDiscovery || {};
  const items = Array.isArray(payload.items) ? payload.items : [];
  const candidates = Array.isArray(payload.block_candidates) ? payload.block_candidates : [];
  const busy = state.dailyDiscoveryRunning ? "disabled" : "";
  return `
    <section class="helper-card daily-discovery-panel">
      <div class="helper-row-head">
        <div>
          <span class="eyebrow">JUE MORNING DISCOVERY</span>
          <h4>쥬 아침 탐사</h4>
          <p>장전 KOSPI 5개 + KOSDAQ 5개 심층 스터디</p>
        </div>
        <button class="btn small" type="button" data-discovery-action="run" ${busy}>심층 탐사 실행</button>
      </div>
      ${state.dailyDiscoveryError ? `<div class="notice warn">${escapeHTML(state.dailyDiscoveryError)}</div>` : ""}
      <div class="strategy-data-strip">
        <span class="strategy-data-chip">상태 ${escapeHTML(payload.status || "missing")}</span>
        <span class="strategy-data-chip">일자 ${escapeHTML(payload.trading_day || "-")}</span>
        <span class="strategy-data-chip good">후보 ${escapeHTML(String(candidates.length))}</span>
      </div>
      <div class="daily-discovery-grid">
        ${items.length ? items.map((row) => {
          const analysis = row.analysis || {};
          return `
            <article class="daily-discovery-card">
              <strong>${escapeHTML(row.name || row.symbol || "-")} <span>${escapeHTML(row.symbol || "")}</span></strong>
              <div class="strategy-data-strip compact">
                <span class="strategy-data-chip">${escapeHTML(row.market || "-")}</span>
                <span class="strategy-data-chip ${analysis.stance === "block_candidate" ? "good" : "neutral"}">${escapeHTML(analysis.stance || "-")}</span>
                <span class="strategy-data-chip">score ${escapeHTML(fmtNum(row.score || 0, 1))}</span>
              </div>
              <p>${escapeHTML(analysis.summary || "분석 요약 대기")}</p>
            </article>
          `;
        }).join("") : '<div class="notice compact">아직 오늘 아침 탐사 결과가 없습니다.</div>'}
      </div>
    </section>
  `;
}
```

Insert `renderDailyDiscoveryPanel()` in the investment helper/research area near memory and block trading panels.

Add event delegation:

```js
if (target.matches("[data-discovery-action='run']")) {
  runDailyDiscovery();
}
```

Add initial load in the same boot path as memory/block status:

```js
loadDailyDiscovery();
```

- [x] **Step 3: Add CSS**

Add:

```css
.daily-discovery-panel {
  display: grid;
  gap: 12px;
}

.daily-discovery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}

.daily-discovery-card {
  display: grid;
  gap: 8px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--surface-soft);
  padding: 10px;
}

.daily-discovery-card strong {
  color: var(--ink);
  line-height: 1.3;
}

.daily-discovery-card strong span {
  color: var(--muted);
  font-size: 12px;
}

.daily-discovery-card p {
  margin: 0;
  color: var(--muted-strong);
  font-size: 13px;
  line-height: 1.45;
}
```

- [x] **Step 4: Run JS checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
tsc --allowJs --checkJs false --noEmit --lib DOM,ES2022 src/tradecraft/web/static/app.js
```

Expected: both commands exit 0.

---

## Task 8: Final Verification

**Files:**
- Verify all changed files.

- [x] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_daily_discovery.py tests/test_daily_discovery_api.py tests/test_kis_block_trader.py tests/test_investment_memory.py -q
```

Expected: pass.

- [x] **Step 2: Run broader smoke**

Run:

```bash
pytest tests/test_api_smoke.py tests/test_config.py tests/test_symbol_analysis.py tests/test_symbol_fundamentals.py -q
```

Expected: pass.

- [x] **Step 3: Run frontend checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
tsc --allowJs --checkJs false --noEmit --lib DOM,ES2022 src/tradecraft/web/static/app.js
```

Expected: both commands exit 0.

- [x] **Step 4: Run lint and whitespace checks**

Run:

```bash
ruff check src/tradecraft/services/daily_discovery.py src/tradecraft/services/naver_reports.py src/tradecraft/services/kis_block_trader.py src/tradecraft/services/investment_memory.py tests/test_daily_discovery.py tests/test_daily_discovery_api.py
git diff --check
```

Expected: `ruff` reports `All checks passed!`; `git diff --check` exits 0.

- [x] **Step 5: Manual local verification**

With the control server running on `127.0.0.1:18080`, run:

```bash
curl -s -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" \
  http://127.0.0.1:18080/api/discovery/status | python3 -m json.tool
```

Expected shape:

```json
{
  "status": "ok",
  "config": {
    "enabled": true,
    "kospi_count": 5,
    "kosdaq_count": 5,
    "exclude_recent_days": 10
  },
  "latest": {
    "status": "missing",
    "items": []
  }
}
```

Manual run:

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"force": true}' \
  http://127.0.0.1:18080/api/discovery/run-once | python3 -m json.tool
```

Expected: `selected_count` is 10 and `analyzed_count` is 10 when LLM and symbol directory are ready. If the symbol directory is empty, the response must return `selected_count` 0 with a clear status rather than crashing.

---

## Self-Review

- Spec coverage: The plan covers KOSPI/KOSDAQ sampling, all 10 deep gpt-5.5 analyses through `SymbolAnalysisService`, persistence, memory integration, block-manager context, API endpoints, UI exposure, and verification.
- Scope check: The plan is one subsystem, `daily_discovery`, and does not modify order execution. Block creation remains gated through the existing KIS block manager.
- Placeholder scan: No step relies on unnamed future work. Each test and implementation step has concrete paths, functions, commands, and expected results.
- Type consistency: The plan consistently uses `DailyDiscoveryConfig`, `DailyDiscoveryRepository`, `DailyDiscoveryService`, `latest_context`, `run_once`, and `daily_discovery` payload keys.
