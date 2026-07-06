# Freqtrade Pattern Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a safe Freqtrade-inspired pattern lab that imports strategy ideas and OHLCV data, backtests them against HERMES crypto time series, and feeds compact pattern scorecards into Binance Jue's block decisions.

**Architecture:** Do not reintroduce Freqtrade as the live execution engine. Add a separate `CryptoPatternLab` service that statically extracts pattern templates from Freqtrade strategy files, evaluates those templates against existing `crypto_klines`, stores scorecards in `.runtime/crypto_pattern_lab.db`, and injects compact pattern context into Binance Jue prompts. Keep live order execution inside the existing Binance block trader and rule executor.

**Tech Stack:** Python 3.10, FastAPI, SQLite, stdlib `ast`, `json`, `gzip`, existing Binance kline collector, existing static frontend, pytest.

---

## File Structure

- Create `src/tradecraft/services/crypto_pattern_lab.py`
  - Owns pattern extraction, Freqtrade data import, SQLite schema, deterministic backtest, and prompt context packing.
- Create `src/tradecraft/runtime/crypto_pattern_lab_runner.py`
  - Periodically imports configured strategy directories and runs bounded pattern backtests over current crypto universe.
- Modify `src/tradecraft/config.py`
  - Adds env-driven settings for pattern lab enablement, DB path, strategy paths, data paths, max symbols, intervals, and retention.
- Modify `src/tradecraft/services/settings_catalog.py`
  - Exposes pattern lab settings in the settings UI.
- Modify `src/tradecraft/runtime/binance_block_trader_runner.py`
  - Wires `CryptoPatternLabService` into `BinanceBlockTrader`.
- Modify `src/tradecraft/main.py`
  - Wires global service and adds admin-protected pattern lab APIs.
- Modify `src/tradecraft/services/binance_block_trader.py`
  - Adds an optional `crypto_pattern_provider` and includes `crypto_patterns` in manager prompts.
- Modify `src/tradecraft/web/static/app.js`
  - Adds a Binance pattern board and fetches the new pattern context API.
- Modify `src/tradecraft/web/static/style.css`
  - Styles pattern board tables/chips.
- Modify `pyproject.toml`
  - Adds `tradecraft-crypto-pattern-lab` entrypoint.
- Test files:
  - Create `tests/test_crypto_pattern_lab.py`
  - Create `tests/test_crypto_pattern_lab_runner.py`
  - Modify `tests/test_binance_block_trader.py`
  - Modify `tests/test_binance_block_trader_runner.py`
  - Modify `tests/test_api_smoke.py`
  - Modify `tests/test_config.py`

Do not add `pandas`, `pyarrow`, `duckdb`, or Freqtrade dependencies in v1. Freqtrade supports Feather/Parquet, but HERMES v1 should import only Freqtrade JSON/JSONGZ OHLCV with stdlib. Add heavy file format support later only if local data volume proves it is worth it.

## Data Model

`crypto_pattern_lab.db` tables:

- `freqtrade_strategy_sources`
  - `source_id TEXT PRIMARY KEY`
  - `path TEXT NOT NULL`
  - `strategy_name TEXT NOT NULL DEFAULT ''`
  - `source_hash TEXT NOT NULL`
  - `imported_at TEXT NOT NULL`
  - `status TEXT NOT NULL`
  - `error_message TEXT NOT NULL DEFAULT ''`

- `strategy_patterns`
  - `pattern_id TEXT PRIMARY KEY`
  - `source_id TEXT NOT NULL`
  - `name TEXT NOT NULL`
  - `family TEXT NOT NULL`
  - `direction TEXT NOT NULL`
  - `timeframe TEXT NOT NULL`
  - `indicators_json TEXT NOT NULL DEFAULT '[]'`
  - `expression_json TEXT NOT NULL DEFAULT '{}'`
  - `risk_tags_json TEXT NOT NULL DEFAULT '[]'`
  - `created_at TEXT NOT NULL`

- `pattern_backtests`
  - `id INTEGER PRIMARY KEY AUTOINCREMENT`
  - `pattern_id TEXT NOT NULL`
  - `symbol TEXT NOT NULL`
  - `interval TEXT NOT NULL`
  - `sample_start TEXT NOT NULL DEFAULT ''`
  - `sample_end TEXT NOT NULL DEFAULT ''`
  - `trade_count INTEGER NOT NULL DEFAULT 0`
  - `win_rate REAL NOT NULL DEFAULT 0`
  - `expectancy_r REAL NOT NULL DEFAULT 0`
  - `avg_r REAL NOT NULL DEFAULT 0`
  - `profit_factor REAL NOT NULL DEFAULT 0`
  - `max_loss_r REAL NOT NULL DEFAULT 0`
  - `mfe_r REAL NOT NULL DEFAULT 0`
  - `mae_r REAL NOT NULL DEFAULT 0`
  - `regime TEXT NOT NULL DEFAULT ''`
  - `score REAL NOT NULL DEFAULT 0`
  - `warnings_json TEXT NOT NULL DEFAULT '[]'`
  - `evaluated_at TEXT NOT NULL`

- `freqtrade_ohlcv_imports`
  - `id INTEGER PRIMARY KEY AUTOINCREMENT`
  - `path TEXT NOT NULL`
  - `symbol TEXT NOT NULL`
  - `interval TEXT NOT NULL`
  - `row_count INTEGER NOT NULL DEFAULT 0`
  - `status TEXT NOT NULL`
  - `error_message TEXT NOT NULL DEFAULT ''`
  - `imported_at TEXT NOT NULL`

---

### Task 1: Pattern Lab Repository And Static Extractor

**Files:**
- Create: `src/tradecraft/services/crypto_pattern_lab.py`
- Test: `tests/test_crypto_pattern_lab.py`

- [ ] **Step 1: Write failing repository and extractor tests**

Add this to `tests/test_crypto_pattern_lab.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from tradecraft.services.crypto_pattern_lab import (
    CryptoPatternLabRepository,
    FreqtradeStrategyExtractor,
)


def test_repository_saves_strategy_source_and_patterns(tmp_path: Path) -> None:
    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_strategy_source(
        {
            "source_id": "sha256:abc",
            "path": "/tmp/SampleStrategy.py",
            "strategy_name": "SampleStrategy",
            "source_hash": "abc",
            "status": "ok",
        }
    )
    repo.save_patterns(
        [
            {
                "pattern_id": "sha256:abc:rsi_mean_reversion:long:5m",
                "source_id": "sha256:abc",
                "name": "SampleStrategy rsi_mean_reversion long",
                "family": "rsi_mean_reversion",
                "direction": "long",
                "timeframe": "5m",
                "indicators": ["rsi", "volume"],
                "expression": {"enter_column": "enter_long"},
                "risk_tags": ["oversold_bounce"],
            }
        ]
    )

    context = repo.pattern_context(symbols=["BTCUSDT"], limit=10)

    assert context["status"] == "ok"
    assert context["patterns"][0]["family"] == "rsi_mean_reversion"
    assert context["patterns"][0]["direction"] == "long"


def test_extractor_detects_freqtrade_patterns_without_executing_code(tmp_path: Path) -> None:
    strategy = tmp_path / "SampleStrategy.py"
    strategy.write_text(
        '''
import os
from freqtrade.strategy import IStrategy

class SampleStrategy(IStrategy):
    timeframe = "5m"

    def populate_indicators(self, dataframe, metadata):
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=8)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=21)
        os.environ["HERMES_SHOULD_NOT_EXECUTE"] = "1"
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[
            (dataframe["rsi"] < 30) & (dataframe["volume"] > 0),
            "enter_long",
        ] = 1
        dataframe.loc[
            (dataframe["ema_fast"] < dataframe["ema_slow"]),
            "enter_short",
        ] = 1
        return dataframe
''',
        encoding="utf-8",
    )

    result = FreqtradeStrategyExtractor().extract_file(strategy)

    assert result["status"] == "ok"
    assert result["strategy_name"] == "SampleStrategy"
    families = {row["family"] for row in result["patterns"]}
    directions = {row["direction"] for row in result["patterns"]}
    assert "rsi_mean_reversion" in families
    assert "ema_trend" in families
    assert {"long", "short"}.issubset(directions)
    assert "HERMES_SHOULD_NOT_EXECUTE" not in __import__("os").environ


def test_extractor_rejects_non_python_file(tmp_path: Path) -> None:
    path = tmp_path / "strategy.txt"
    path.write_text("not python", encoding="utf-8")

    result = FreqtradeStrategyExtractor().extract_file(path)

    assert result["status"] == "error"
    assert "python" in result["error_message"].lower()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_crypto_pattern_lab.py::test_repository_saves_strategy_source_and_patterns tests/test_crypto_pattern_lab.py::test_extractor_detects_freqtrade_patterns_without_executing_code tests/test_crypto_pattern_lab.py::test_extractor_rejects_non_python_file -q
```

Expected:

```text
ModuleNotFoundError: No module named 'tradecraft.services.crypto_pattern_lab'
```

- [ ] **Step 3: Implement repository and extractor**

Create `src/tradecraft/services/crypto_pattern_lab.py` with:

```python
from __future__ import annotations

import ast
import gzip
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


def stable_id(*parts: Any) -> str:
    text = "|".join(str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def normalize_symbol(symbol: Any) -> str:
    text = str(symbol or "").upper().strip()
    return text.replace("/", "").replace(":", "").replace("-", "")


def safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


@dataclass(slots=True)
class CryptoPatternLabConfig:
    db_path: str = ".runtime/crypto_pattern_lab.db"
    enabled: bool = True
    strategy_paths: str = ""
    freqtrade_data_paths: str = ""
    max_symbols: int = 30
    intervals: str = "5m,15m,1h"
    lookback_bars: int = 500
    context_limit: int = 12
    retention_days: int = 90


class CryptoPatternLabRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS freqtrade_strategy_sources (
                    source_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    strategy_name TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS strategy_patterns (
                    pattern_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    family TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    indicators_json TEXT NOT NULL DEFAULT '[]',
                    expression_json TEXT NOT NULL DEFAULT '{}',
                    risk_tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_strategy_patterns_family
                    ON strategy_patterns(family, direction, timeframe);
                CREATE TABLE IF NOT EXISTS pattern_backtests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    sample_start TEXT NOT NULL DEFAULT '',
                    sample_end TEXT NOT NULL DEFAULT '',
                    trade_count INTEGER NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0,
                    expectancy_r REAL NOT NULL DEFAULT 0,
                    avg_r REAL NOT NULL DEFAULT 0,
                    profit_factor REAL NOT NULL DEFAULT 0,
                    max_loss_r REAL NOT NULL DEFAULT 0,
                    mfe_r REAL NOT NULL DEFAULT 0,
                    mae_r REAL NOT NULL DEFAULT 0,
                    regime TEXT NOT NULL DEFAULT '',
                    score REAL NOT NULL DEFAULT 0,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    evaluated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pattern_backtests_symbol_time
                    ON pattern_backtests(symbol, interval, evaluated_at DESC);
                CREATE TABLE IF NOT EXISTS freqtrade_ohlcv_imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    imported_at TEXT NOT NULL
                );
                """
            )

    def save_strategy_source(self, payload: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO freqtrade_strategy_sources (
                    source_id, path, strategy_name, source_hash, imported_at, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    path=excluded.path,
                    strategy_name=excluded.strategy_name,
                    source_hash=excluded.source_hash,
                    imported_at=excluded.imported_at,
                    status=excluded.status,
                    error_message=excluded.error_message
                """,
                (
                    str(payload["source_id"]),
                    str(payload.get("path") or ""),
                    str(payload.get("strategy_name") or ""),
                    str(payload.get("source_hash") or ""),
                    str(payload.get("imported_at") or now),
                    str(payload.get("status") or "ok"),
                    str(payload.get("error_message") or ""),
                ),
            )

    def save_patterns(self, patterns: list[dict[str, Any]]) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            for pattern in patterns:
                conn.execute(
                    """
                    INSERT INTO strategy_patterns (
                        pattern_id, source_id, name, family, direction, timeframe,
                        indicators_json, expression_json, risk_tags_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(pattern_id) DO UPDATE SET
                        name=excluded.name,
                        family=excluded.family,
                        direction=excluded.direction,
                        timeframe=excluded.timeframe,
                        indicators_json=excluded.indicators_json,
                        expression_json=excluded.expression_json,
                        risk_tags_json=excluded.risk_tags_json
                    """,
                    (
                        str(pattern["pattern_id"]),
                        str(pattern.get("source_id") or ""),
                        str(pattern.get("name") or ""),
                        str(pattern.get("family") or ""),
                        str(pattern.get("direction") or "long"),
                        str(pattern.get("timeframe") or "15m"),
                        json_dumps(pattern.get("indicators") or []),
                        json_dumps(pattern.get("expression") or {}),
                        json_dumps(pattern.get("risk_tags") or []),
                        str(pattern.get("created_at") or now),
                    ),
                )

    def list_patterns(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM strategy_patterns
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [self._row_to_pattern(row) for row in rows]

    def save_backtest(self, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pattern_backtests (
                    pattern_id, symbol, interval, sample_start, sample_end, trade_count,
                    win_rate, expectancy_r, avg_r, profit_factor, max_loss_r,
                    mfe_r, mae_r, regime, score, warnings_json, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("pattern_id") or ""),
                    normalize_symbol(payload.get("symbol")),
                    str(payload.get("interval") or ""),
                    str(payload.get("sample_start") or ""),
                    str(payload.get("sample_end") or ""),
                    int(payload.get("trade_count") or 0),
                    safe_float(payload.get("win_rate")),
                    safe_float(payload.get("expectancy_r")),
                    safe_float(payload.get("avg_r")),
                    safe_float(payload.get("profit_factor")),
                    safe_float(payload.get("max_loss_r")),
                    safe_float(payload.get("mfe_r")),
                    safe_float(payload.get("mae_r")),
                    str(payload.get("regime") or ""),
                    safe_float(payload.get("score")),
                    json_dumps(payload.get("warnings") or []),
                    str(payload.get("evaluated_at") or utc_now_iso()),
                ),
            )

    def latest_scorecards(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clean_symbols = [normalize_symbol(symbol) for symbol in symbols or [] if normalize_symbol(symbol)]
        params: list[Any] = []
        where = ""
        if clean_symbols:
            placeholders = ",".join("?" for _ in clean_symbols)
            where = f"WHERE b.symbol IN ({placeholders})"
            params.extend(clean_symbols)
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT b.*, p.family, p.direction, p.name, p.risk_tags_json
                FROM pattern_backtests b
                LEFT JOIN strategy_patterns p ON p.pattern_id = b.pattern_id
                {where}
                ORDER BY b.score DESC, b.evaluated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_scorecard(row) for row in rows]

    def pattern_context(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        patterns = self.list_patterns(limit=limit)
        scorecards = self.latest_scorecards(symbols=symbols, limit=limit)
        return {
            "status": "ok",
            "patterns": patterns,
            "scorecards": scorecards,
            "policy": {
                "meaning": "Use pattern scorecards as empirical context. They adjust conviction and sizing, not live execution gates.",
                "source": "freqtrade_static_extract_plus_hermes_backtest",
            },
        }

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            source_count = conn.execute("SELECT COUNT(*) FROM freqtrade_strategy_sources").fetchone()[0]
            pattern_count = conn.execute("SELECT COUNT(*) FROM strategy_patterns").fetchone()[0]
            backtest_count = conn.execute("SELECT COUNT(*) FROM pattern_backtests").fetchone()[0]
        return {
            "status": "ok",
            "db_path": str(self.path),
            "source_count": int(source_count),
            "pattern_count": int(pattern_count),
            "backtest_count": int(backtest_count),
        }

    def _row_to_pattern(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "pattern_id": row["pattern_id"],
            "source_id": row["source_id"],
            "name": row["name"],
            "family": row["family"],
            "direction": row["direction"],
            "timeframe": row["timeframe"],
            "indicators": json_loads(row["indicators_json"], []),
            "expression": json_loads(row["expression_json"], {}),
            "risk_tags": json_loads(row["risk_tags_json"], []),
            "created_at": row["created_at"],
        }

    def _row_to_scorecard(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "pattern_id": row["pattern_id"],
            "symbol": row["symbol"],
            "interval": row["interval"],
            "family": row["family"],
            "direction": row["direction"],
            "name": row["name"],
            "trade_count": int(row["trade_count"]),
            "win_rate": float(row["win_rate"]),
            "expectancy_r": float(row["expectancy_r"]),
            "profit_factor": float(row["profit_factor"]),
            "score": float(row["score"]),
            "warnings": json_loads(row["warnings_json"], []),
            "evaluated_at": row["evaluated_at"],
        }


class FreqtradeStrategyExtractor:
    def extract_file(self, path: str | Path) -> dict[str, Any]:
        file_path = Path(path)
        if file_path.suffix != ".py":
            return {
                "status": "error",
                "path": str(file_path),
                "error_message": "Freqtrade strategy source must be a Python .py file",
                "patterns": [],
            }
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception as exc:
            return {
                "status": "error",
                "path": str(file_path),
                "error_message": str(exc),
                "patterns": [],
            }
        source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        strategy_name = self._strategy_name(tree)
        timeframe = self._timeframe(tree) or "15m"
        indicators = self._indicator_names(tree)
        directions = self._directions(tree)
        families = self._families(indicators=indicators, tree=tree)
        patterns = []
        source_id = f"sha256:{source_hash[:24]}"
        for family in families:
            for direction in directions:
                pattern_id = f"{source_id}:{family}:{direction}:{timeframe}"
                patterns.append(
                    {
                        "pattern_id": pattern_id,
                        "source_id": source_id,
                        "name": f"{strategy_name} {family} {direction}",
                        "family": family,
                        "direction": direction,
                        "timeframe": timeframe,
                        "indicators": sorted(indicators),
                        "expression": {
                            "enter_column": "enter_short" if direction == "short" else "enter_long",
                            "source": "static_ast",
                        },
                        "risk_tags": self._risk_tags(family),
                    }
                )
        return {
            "status": "ok",
            "path": str(file_path),
            "source_id": source_id,
            "source_hash": source_hash,
            "strategy_name": strategy_name,
            "patterns": patterns,
        }

    def _strategy_name(self, tree: ast.AST) -> str:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                return node.name
        return "FreqtradeStrategy"

    def _timeframe(self, tree: ast.AST) -> str:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                if "timeframe" in names and isinstance(node.value, ast.Constant):
                    return str(node.value.value)
        return ""

    def _indicator_names(self, tree: ast.AST) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                value = str(node.slice.value).lower()
                if value in {"rsi", "ema_fast", "ema_slow", "macd", "bb_lowerband", "bb_upperband", "volume"}:
                    names.add(value)
            if isinstance(node, ast.Attribute):
                attr = node.attr.lower()
                if attr in {"rsi", "ema", "macd", "bollinger_bands"}:
                    names.add(attr)
        return names

    def _directions(self, tree: ast.AST) -> list[str]:
        text = ast.unparse(tree).lower() if hasattr(ast, "unparse") else ""
        directions = []
        if "enter_long" in text or "buy" in text:
            directions.append("long")
        if "enter_short" in text or "short" in text:
            directions.append("short")
        return directions or ["long"]

    def _families(self, *, indicators: set[str], tree: ast.AST) -> list[str]:
        text = ast.unparse(tree).lower() if hasattr(ast, "unparse") else ""
        families: list[str] = []
        if "rsi" in indicators:
            families.append("rsi_mean_reversion")
        if "ema" in indicators or {"ema_fast", "ema_slow"}.intersection(indicators):
            families.append("ema_trend")
        if "bollinger_bands" in indicators or "bb_lowerband" in indicators or "bb_upperband" in indicators:
            families.append("bollinger_squeeze")
        if "macd" in indicators:
            families.append("macd_momentum")
        if "volume" in text:
            families.append("volume_confirmation")
        return list(dict.fromkeys(families or ["generic_price_action"]))

    def _risk_tags(self, family: str) -> list[str]:
        mapping = {
            "rsi_mean_reversion": ["mean_reversion", "can_fight_trend"],
            "ema_trend": ["trend_following", "late_entry_risk"],
            "bollinger_squeeze": ["volatility_expansion", "false_breakout_risk"],
            "macd_momentum": ["momentum", "lagging_indicator"],
            "volume_confirmation": ["liquidity_sensitive"],
        }
        return mapping.get(family, ["needs_manual_review"])
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
pytest tests/test_crypto_pattern_lab.py::test_repository_saves_strategy_source_and_patterns tests/test_crypto_pattern_lab.py::test_extractor_detects_freqtrade_patterns_without_executing_code tests/test_crypto_pattern_lab.py::test_extractor_rejects_non_python_file -q
```

Expected:

```text
... [100%]
```

---

### Task 2: Freqtrade JSON/JSONGZ OHLCV Import And Existing Kline Reader

**Files:**
- Modify: `src/tradecraft/services/crypto_pattern_lab.py`
- Test: `tests/test_crypto_pattern_lab.py`

- [ ] **Step 1: Write failing import and kline reader tests**

Append to `tests/test_crypto_pattern_lab.py`:

```python
import gzip
import json
import sqlite3

from tradecraft.services.crypto_pattern_lab import (
    FreqtradeOHLCVImporter,
    HermesKlineReader,
)


def test_freqtrade_json_ohlcv_importer_normalizes_rows(tmp_path: Path) -> None:
    path = tmp_path / "BTC_USDT-5m.json"
    path.write_text(
        json.dumps(
            [
                [1700000000000, 100.0, 110.0, 95.0, 105.0, 1234.0],
                [1700000300000, 105.0, 112.0, 101.0, 108.0, 999.0],
            ]
        ),
        encoding="utf-8",
    )

    rows = FreqtradeOHLCVImporter().read_file(path, symbol="BTCUSDT", interval="5m")

    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["interval"] == "5m"
    assert rows[0]["open_time"] == 1700000000000
    assert rows[0]["close"] == 105.0
    assert rows[0]["volume"] == 1234.0


def test_freqtrade_jsongz_ohlcv_importer_normalizes_rows(tmp_path: Path) -> None:
    path = tmp_path / "ETH_USDT-15m.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump([[1700000000000, 10, 11, 9, 10.5, 88]], handle)

    rows = FreqtradeOHLCVImporter().read_file(path, symbol="ETHUSDT", interval="15m")

    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETHUSDT"
    assert rows[0]["close"] == 10.5


def test_hermes_kline_reader_reads_existing_crypto_klines(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto_market_research.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE crypto_klines (
                symbol TEXT,
                market TEXT,
                interval TEXT,
                open_time INTEGER,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                quote_volume REAL,
                close_time INTEGER,
                raw_json TEXT,
                PRIMARY KEY(symbol, market, interval, open_time)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO crypto_klines VALUES (
                'BTCUSDT', 'spot', '5m', 1, 100, 110, 90, 105, 1000, 105000, 2, '{}'
            )
            """
        )

    rows = HermesKlineReader(db_path).read(symbol="BTCUSDT", interval="5m", limit=10)

    assert len(rows) == 1
    assert rows[0]["close"] == 105.0
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_crypto_pattern_lab.py::test_freqtrade_json_ohlcv_importer_normalizes_rows tests/test_crypto_pattern_lab.py::test_freqtrade_jsongz_ohlcv_importer_normalizes_rows tests/test_crypto_pattern_lab.py::test_hermes_kline_reader_reads_existing_crypto_klines -q
```

Expected:

```text
ImportError: cannot import name 'FreqtradeOHLCVImporter'
```

- [ ] **Step 3: Implement importers**

Append to `src/tradecraft/services/crypto_pattern_lab.py`:

```python
class FreqtradeOHLCVImporter:
    def read_file(
        self,
        path: str | Path,
        *,
        symbol: str,
        interval: str,
    ) -> list[dict[str, Any]]:
        file_path = Path(path)
        if file_path.suffix == ".gz" or file_path.name.endswith(".json.gz"):
            with gzip.open(file_path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        rows: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, list) or len(item) < 6:
                continue
            open_time = int(safe_float(item[0]))
            rows.append(
                {
                    "symbol": normalize_symbol(symbol),
                    "interval": str(interval),
                    "open_time": open_time,
                    "open": safe_float(item[1]),
                    "high": safe_float(item[2]),
                    "low": safe_float(item[3]),
                    "close": safe_float(item[4]),
                    "volume": safe_float(item[5]),
                    "quote_volume": safe_float(item[5]) * safe_float(item[4]),
                    "close_time": open_time,
                }
            )
        return rows


class HermesKlineReader:
    def __init__(self, crypto_market_db_path: str | Path) -> None:
        self.path = Path(crypto_market_db_path)

    def read(
        self,
        *,
        symbol: str,
        interval: str,
        market: str = "spot",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(str(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT symbol, interval, open_time, open, high, low, close,
                       volume, quote_volume, close_time
                FROM crypto_klines
                WHERE symbol = ? AND market = ? AND interval = ?
                ORDER BY open_time DESC
                LIMIT ?
                """,
                (normalize_symbol(symbol), str(market), str(interval), max(int(limit), 1)),
            ).fetchall()
        return [
            {
                "symbol": row["symbol"],
                "interval": row["interval"],
                "open_time": int(row["open_time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "quote_volume": float(row["quote_volume"]),
                "close_time": int(row["close_time"]),
            }
            for row in reversed(rows)
        ]
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
pytest tests/test_crypto_pattern_lab.py::test_freqtrade_json_ohlcv_importer_normalizes_rows tests/test_crypto_pattern_lab.py::test_freqtrade_jsongz_ohlcv_importer_normalizes_rows tests/test_crypto_pattern_lab.py::test_hermes_kline_reader_reads_existing_crypto_klines -q
```

Expected:

```text
... [100%]
```

---

### Task 3: Deterministic Pattern Backtest Lab

**Files:**
- Modify: `src/tradecraft/services/crypto_pattern_lab.py`
- Test: `tests/test_crypto_pattern_lab.py`

- [ ] **Step 1: Write failing backtest tests**

Append to `tests/test_crypto_pattern_lab.py`:

```python
from tradecraft.services.crypto_pattern_lab import PatternBacktestLab


def test_pattern_backtest_lab_scores_ema_trend_long() -> None:
    rows = [
        {"open_time": index, "open": 100 + index, "high": 102 + index, "low": 99 + index, "close": 101 + index, "volume": 1000 + index}
        for index in range(80)
    ]
    pattern = {
        "pattern_id": "p1",
        "family": "ema_trend",
        "direction": "long",
        "timeframe": "5m",
    }

    result = PatternBacktestLab().evaluate(pattern=pattern, symbol="BTCUSDT", interval="5m", rows=rows)

    assert result["symbol"] == "BTCUSDT"
    assert result["pattern_id"] == "p1"
    assert result["trade_count"] > 0
    assert result["score"] > 0
    assert "expectancy_r" in result


def test_pattern_backtest_lab_requires_enough_rows() -> None:
    pattern = {
        "pattern_id": "p1",
        "family": "rsi_mean_reversion",
        "direction": "long",
        "timeframe": "5m",
    }

    result = PatternBacktestLab().evaluate(pattern=pattern, symbol="BTCUSDT", interval="5m", rows=[])

    assert result["trade_count"] == 0
    assert result["score"] == 0
    assert "insufficient_rows" in result["warnings"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_crypto_pattern_lab.py::test_pattern_backtest_lab_scores_ema_trend_long tests/test_crypto_pattern_lab.py::test_pattern_backtest_lab_requires_enough_rows -q
```

Expected:

```text
ImportError: cannot import name 'PatternBacktestLab'
```

- [ ] **Step 3: Implement backtest lab**

Append to `src/tradecraft/services/crypto_pattern_lab.py`:

```python
class PatternBacktestLab:
    def evaluate(
        self,
        *,
        pattern: dict[str, Any],
        symbol: str,
        interval: str,
        rows: list[dict[str, Any]],
        max_trades: int = 80,
    ) -> dict[str, Any]:
        clean_rows = [row for row in rows if safe_float(row.get("close")) > 0]
        pattern_id = str(pattern.get("pattern_id") or "")
        family = str(pattern.get("family") or "generic_price_action")
        direction = str(pattern.get("direction") or "long")
        warnings: list[str] = []
        if len(clean_rows) < 30:
            return {
                "pattern_id": pattern_id,
                "symbol": normalize_symbol(symbol),
                "interval": interval,
                "trade_count": 0,
                "win_rate": 0.0,
                "expectancy_r": 0.0,
                "avg_r": 0.0,
                "profit_factor": 0.0,
                "max_loss_r": 0.0,
                "mfe_r": 0.0,
                "mae_r": 0.0,
                "score": 0.0,
                "warnings": ["insufficient_rows"],
                "sample_start": "",
                "sample_end": "",
            }

        trades: list[float] = []
        mfe_values: list[float] = []
        mae_values: list[float] = []
        for index in range(21, len(clean_rows) - 6):
            if len(trades) >= max_trades:
                break
            if not self._entry_signal(family=family, direction=direction, rows=clean_rows, index=index):
                continue
            outcome = self._simulate_trade(direction=direction, rows=clean_rows[index : index + 7])
            trades.append(outcome["r"])
            mfe_values.append(outcome["mfe_r"])
            mae_values.append(outcome["mae_r"])

        wins = [value for value in trades if value > 0]
        losses = [value for value in trades if value <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        trade_count = len(trades)
        win_rate = len(wins) / trade_count if trade_count else 0.0
        avg_r = sum(trades) / trade_count if trade_count else 0.0
        profit_factor = gross_win / gross_loss if gross_loss > 0 else gross_win
        max_loss_r = min(trades) if trades else 0.0
        expectancy_r = avg_r
        if trade_count < 5:
            warnings.append("low_sample")
        if profit_factor < 1.0 and trade_count:
            warnings.append("negative_profit_factor")
        score = max(min((expectancy_r * 35.0) + (win_rate * 35.0) + min(profit_factor, 3.0) * 10.0, 100.0), 0.0)
        return {
            "pattern_id": pattern_id,
            "symbol": normalize_symbol(symbol),
            "interval": interval,
            "sample_start": str(clean_rows[0].get("open_time") or ""),
            "sample_end": str(clean_rows[-1].get("open_time") or ""),
            "trade_count": trade_count,
            "win_rate": round(win_rate, 4),
            "expectancy_r": round(expectancy_r, 4),
            "avg_r": round(avg_r, 4),
            "profit_factor": round(profit_factor, 4),
            "max_loss_r": round(max_loss_r, 4),
            "mfe_r": round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else 0.0,
            "mae_r": round(sum(mae_values) / len(mae_values), 4) if mae_values else 0.0,
            "score": round(score, 2),
            "warnings": warnings,
        }

    def _entry_signal(
        self,
        *,
        family: str,
        direction: str,
        rows: list[dict[str, Any]],
        index: int,
    ) -> bool:
        closes = [safe_float(row.get("close")) for row in rows[: index + 1]]
        volumes = [safe_float(row.get("volume")) for row in rows[: index + 1]]
        recent = closes[-14:]
        previous = closes[-21:-7]
        if len(recent) < 14 or len(previous) < 10:
            return False
        momentum = (recent[-1] - recent[0]) / recent[0] * 100.0 if recent[0] > 0 else 0.0
        prior_momentum = (previous[-1] - previous[0]) / previous[0] * 100.0 if previous[0] > 0 else 0.0
        avg_volume = sum(volumes[-20:-1]) / max(len(volumes[-20:-1]), 1)
        volume_ok = volumes[-1] >= avg_volume * 0.8
        if family == "ema_trend":
            return momentum > 0.2 if direction == "long" else momentum < -0.2
        if family == "rsi_mean_reversion":
            return prior_momentum < -1.0 and momentum > -0.2 if direction == "long" else prior_momentum > 1.0 and momentum < 0.2
        if family == "volume_confirmation":
            return volume_ok and abs(momentum) > 0.25
        if family == "bollinger_squeeze":
            recent_range = max(recent) - min(recent)
            return recent_range / recent[-1] < 0.03 if recent[-1] > 0 else False
        return abs(momentum) > 0.3

    def _simulate_trade(
        self,
        *,
        direction: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, float]:
        entry = safe_float(rows[0].get("close"))
        if entry <= 0:
            return {"r": 0.0, "mfe_r": 0.0, "mae_r": 0.0}
        stop_pct = 0.012
        target_pct = 0.024
        risk = entry * stop_pct
        if direction == "short":
            stop = entry * (1.0 + stop_pct)
            target = entry * (1.0 - target_pct)
        else:
            stop = entry * (1.0 - stop_pct)
            target = entry * (1.0 + target_pct)
        exit_price = safe_float(rows[-1].get("close"))
        mfe_r = 0.0
        mae_r = 0.0
        for row in rows[1:]:
            high = safe_float(row.get("high"))
            low = safe_float(row.get("low"))
            if direction == "short":
                mfe_r = max(mfe_r, (entry - low) / risk)
                mae_r = min(mae_r, (entry - high) / risk)
                if high >= stop:
                    exit_price = stop
                    break
                if low <= target:
                    exit_price = target
                    break
            else:
                mfe_r = max(mfe_r, (high - entry) / risk)
                mae_r = min(mae_r, (low - entry) / risk)
                if low <= stop:
                    exit_price = stop
                    break
                if high >= target:
                    exit_price = target
                    break
        r_value = ((entry - exit_price) / risk) if direction == "short" else ((exit_price - entry) / risk)
        return {"r": round(r_value, 4), "mfe_r": round(mfe_r, 4), "mae_r": round(mae_r, 4)}
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
pytest tests/test_crypto_pattern_lab.py::test_pattern_backtest_lab_scores_ema_trend_long tests/test_crypto_pattern_lab.py::test_pattern_backtest_lab_requires_enough_rows -q
```

Expected:

```text
.. [100%]
```

---

### Task 4: CryptoPatternLabService Orchestration

**Files:**
- Modify: `src/tradecraft/services/crypto_pattern_lab.py`
- Test: `tests/test_crypto_pattern_lab.py`

- [ ] **Step 1: Write failing service test**

Append to `tests/test_crypto_pattern_lab.py`:

```python
from tradecraft.services.crypto_pattern_lab import (
    CryptoPatternLabConfig,
    CryptoPatternLabService,
)


def test_pattern_lab_service_imports_and_backtests_strategy(tmp_path: Path) -> None:
    strategy = tmp_path / "SampleStrategy.py"
    strategy.write_text(
        '''
class SampleStrategy:
    timeframe = "5m"
    def populate_indicators(self, dataframe, metadata):
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=8)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=21)
        return dataframe
    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[(dataframe["ema_fast"] > dataframe["ema_slow"]), "enter_long"] = 1
        return dataframe
''',
        encoding="utf-8",
    )

    class Reader:
        def read(self, *, symbol: str, interval: str, market: str = "spot", limit: int = 500):
            return [
                {"open_time": index, "open": 100 + index, "high": 102 + index, "low": 99 + index, "close": 101 + index, "volume": 1000 + index}
                for index in range(100)
            ]

    service = CryptoPatternLabService(
        config=CryptoPatternLabConfig(
            db_path=str(tmp_path / "patterns.db"),
            strategy_paths=str(strategy),
            max_symbols=1,
            intervals="5m",
        ),
        kline_reader=Reader(),
    )

    result = service.run_once(symbols=["BTCUSDT"])
    context = service.context_pack(symbols=["BTCUSDT"], limit=5)

    assert result["status"] == "ok"
    assert result["imported_source_count"] == 1
    assert result["backtest_count"] > 0
    assert context["scorecards"][0]["symbol"] == "BTCUSDT"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_crypto_pattern_lab.py::test_pattern_lab_service_imports_and_backtests_strategy -q
```

Expected:

```text
ImportError: cannot import name 'CryptoPatternLabService'
```

- [ ] **Step 3: Implement service**

Append to `src/tradecraft/services/crypto_pattern_lab.py`:

```python
class CryptoPatternLabService:
    def __init__(
        self,
        *,
        config: CryptoPatternLabConfig | None = None,
        repository: CryptoPatternLabRepository | None = None,
        kline_reader: Any | None = None,
    ) -> None:
        self.config = config or CryptoPatternLabConfig()
        self.repository = repository or CryptoPatternLabRepository(self.config.db_path)
        self.extractor = FreqtradeStrategyExtractor()
        self.backtest_lab = PatternBacktestLab()
        self.kline_reader = kline_reader

    def run_once(self, *, symbols: list[str]) -> dict[str, Any]:
        if not self.config.enabled:
            return {"status": "disabled", "imported_source_count": 0, "backtest_count": 0}
        imported = self.import_strategies()
        backtests = self.run_backtests(symbols=symbols)
        return {
            "status": "ok",
            "imported_source_count": int(imported.get("imported_source_count") or 0),
            "pattern_count": int(imported.get("pattern_count") or 0),
            "backtest_count": int(backtests.get("backtest_count") or 0),
            "errors": [*imported.get("errors", []), *backtests.get("errors", [])][:10],
        }

    def import_strategies(self) -> dict[str, Any]:
        paths = self._strategy_files()
        errors: list[dict[str, str]] = []
        imported = 0
        pattern_count = 0
        for path in paths:
            result = self.extractor.extract_file(path)
            if result.get("status") != "ok":
                errors.append({"path": str(path), "error_message": str(result.get("error_message") or "")})
                continue
            self.repository.save_strategy_source(
                {
                    "source_id": result["source_id"],
                    "path": result["path"],
                    "strategy_name": result["strategy_name"],
                    "source_hash": result["source_hash"],
                    "status": "ok",
                }
            )
            self.repository.save_patterns(list(result.get("patterns") or []))
            imported += 1
            pattern_count += len(result.get("patterns") or [])
        return {
            "status": "ok" if not errors else "partial",
            "imported_source_count": imported,
            "pattern_count": pattern_count,
            "errors": errors,
        }

    def run_backtests(self, *, symbols: list[str]) -> dict[str, Any]:
        if self.kline_reader is None:
            return {
                "status": "skipped",
                "backtest_count": 0,
                "errors": [{"symbol": "", "error_message": "kline_reader missing"}],
            }
        patterns = self.repository.list_patterns(limit=200)
        intervals = self._intervals()
        clean_symbols = [normalize_symbol(symbol) for symbol in symbols if normalize_symbol(symbol)]
        clean_symbols = clean_symbols[: max(int(self.config.max_symbols), 1)]
        errors: list[dict[str, str]] = []
        count = 0
        for pattern in patterns:
            for symbol in clean_symbols:
                for interval in intervals:
                    try:
                        rows = self.kline_reader.read(
                            symbol=symbol,
                            interval=interval,
                            limit=max(int(self.config.lookback_bars), 30),
                        )
                        result = self.backtest_lab.evaluate(
                            pattern=pattern,
                            symbol=symbol,
                            interval=interval,
                            rows=rows,
                        )
                        self.repository.save_backtest(result)
                        count += 1
                    except Exception as exc:
                        errors.append({"symbol": symbol, "error_message": str(exc)})
        return {"status": "ok" if not errors else "partial", "backtest_count": count, "errors": errors[:10]}

    def context_pack(self, *, symbols: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
        return self.repository.pattern_context(
            symbols=symbols or [],
            limit=limit or int(self.config.context_limit),
        )

    def status(self) -> dict[str, Any]:
        return {
            **self.repository.status(),
            "enabled": bool(self.config.enabled),
            "strategy_paths": self.config.strategy_paths,
            "intervals": self._intervals(),
            "max_symbols": int(self.config.max_symbols),
        }

    def _strategy_files(self) -> list[Path]:
        out: list[Path] = []
        for raw in str(self.config.strategy_paths or "").replace(";", ",").split(","):
            text = raw.strip()
            if not text:
                continue
            path = Path(text).expanduser()
            if path.is_dir():
                out.extend(sorted(path.rglob("*.py")))
            elif path.is_file():
                out.append(path)
        return list(dict.fromkeys(out))

    def _intervals(self) -> list[str]:
        return [
            item.strip()
            for item in str(self.config.intervals or "5m,15m,1h").replace(";", ",").split(",")
            if item.strip()
        ]
```

- [ ] **Step 4: Run test to verify pass**

Run:

```bash
pytest tests/test_crypto_pattern_lab.py::test_pattern_lab_service_imports_and_backtests_strategy -q
```

Expected:

```text
. [100%]
```

---

### Task 5: Config, Settings UI Catalog, And Runner

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `src/tradecraft/services/settings_catalog.py`
- Modify: `pyproject.toml`
- Create: `src/tradecraft/runtime/crypto_pattern_lab_runner.py`
- Create: `tests/test_crypto_pattern_lab_runner.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing config tests**

Append to `tests/test_config.py`:

```python
def test_crypto_pattern_lab_defaults() -> None:
    settings = AppSettings()

    assert settings.crypto_pattern_lab_enabled is True
    assert settings.crypto_pattern_lab_db_path == ".runtime/crypto_pattern_lab.db"
    assert settings.crypto_pattern_lab_intervals == "5m,15m,1h"
    assert settings.crypto_pattern_lab_context_limit == 12


def test_crypto_pattern_lab_short_env_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_PATTERN_LAB_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_PATTERN_LAB_DB_PATH", "/tmp/patterns.db")

    settings = AppSettings()

    assert settings.crypto_pattern_lab_enabled is False
    assert settings.crypto_pattern_lab_db_path == "/tmp/patterns.db"
```

- [ ] **Step 2: Write failing runner test**

Create `tests/test_crypto_pattern_lab_runner.py`:

```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from tradecraft.runtime.crypto_pattern_lab_runner import run_crypto_pattern_lab_loop


def test_crypto_pattern_lab_runner_writes_state_once(tmp_path: Path, monkeypatch) -> None:
    state_path = tmp_path / "pattern_lab.json"
    calls: list[dict[str, Any]] = []

    class FakeService:
        def run_once(self, *, symbols: list[str]) -> dict[str, Any]:
            calls.append({"symbols": symbols})
            return {"status": "ok", "imported_source_count": 1, "backtest_count": 2}

        def status(self) -> dict[str, Any]:
            return {"status": "ok", "pattern_count": 2, "backtest_count": 2}

    class Settings:
        crypto_pattern_lab_enabled = True
        crypto_pattern_lab_once = True
        crypto_pattern_lab_state_path = str(state_path)
        crypto_pattern_lab_interval_sec = 1
        crypto_market_research_universe = "BTCUSDT,ETHUSDT"
        crypto_pattern_lab_max_symbols = 1

    asyncio.run(
        run_crypto_pattern_lab_loop(
            settings=Settings(),
            service=FakeService(),
            sleep=lambda _: asyncio.sleep(0),
        )
    )

    assert calls == [{"symbols": ["BTCUSDT"]}]
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["result"]["backtest_count"] == 2
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
pytest tests/test_config.py::test_crypto_pattern_lab_defaults tests/test_config.py::test_crypto_pattern_lab_short_env_aliases tests/test_crypto_pattern_lab_runner.py -q
```

Expected:

```text
AttributeError: 'AppSettings' object has no attribute 'crypto_pattern_lab_enabled'
```

- [ ] **Step 4: Add settings**

Add these fields near the crypto quant settings in `src/tradecraft/config.py`:

```python
    crypto_pattern_lab_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "TRADECRAFT_CRYPTO_PATTERN_LAB_ENABLED",
            "CRYPTO_PATTERN_LAB_ENABLED",
        ),
    )
    crypto_pattern_lab_once: bool = Field(
        default=False,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_ONCE",
    )
    crypto_pattern_lab_state_path: str = Field(
        default=".runtime/crypto_pattern_lab.json",
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_STATE_PATH",
    )
    crypto_pattern_lab_db_path: str = Field(
        default=".runtime/crypto_pattern_lab.db",
        validation_alias=AliasChoices(
            "TRADECRAFT_CRYPTO_PATTERN_LAB_DB_PATH",
            "CRYPTO_PATTERN_LAB_DB_PATH",
        ),
    )
    crypto_pattern_lab_strategy_paths: str = Field(
        default="",
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_STRATEGY_PATHS",
    )
    crypto_pattern_lab_freqtrade_data_paths: str = Field(
        default="",
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_FREQTRADE_DATA_PATHS",
    )
    crypto_pattern_lab_interval_sec: int = Field(
        default=3600,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_INTERVAL_SEC",
    )
    crypto_pattern_lab_max_symbols: int = Field(
        default=30,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_MAX_SYMBOLS",
    )
    crypto_pattern_lab_intervals: str = Field(
        default="5m,15m,1h",
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_INTERVALS",
    )
    crypto_pattern_lab_lookback_bars: int = Field(
        default=500,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_LOOKBACK_BARS",
    )
    crypto_pattern_lab_context_limit: int = Field(
        default=12,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_CONTEXT_LIMIT",
    )
    crypto_pattern_lab_retention_days: int = Field(
        default=90,
        alias="TRADECRAFT_CRYPTO_PATTERN_LAB_RETENTION_DAYS",
    )
```

- [ ] **Step 5: Add setting catalog entries**

Add these entries in `src/tradecraft/services/settings_catalog.py` near crypto quant settings:

```python
    "crypto_pattern_lab_enabled": SettingMeta(
        "Freqtrade 패턴 랩",
        "Freqtrade 계열 전략 아이디어를 정적 분석하고 HERMES 시계열로 재검증합니다.",
        "signals",
    ),
    "crypto_pattern_lab_db_path": SettingMeta(
        "패턴 랩 DB",
        "전략 패턴과 백테스트 scorecard를 저장하는 DB 경로입니다.",
        "signals",
    ),
    "crypto_pattern_lab_strategy_paths": SettingMeta(
        "Freqtrade 전략 경로",
        "정적 분석할 Freqtrade 전략 파일/폴더 목록입니다. 쉼표로 구분합니다.",
        "signals",
        input_type="textarea",
    ),
    "crypto_pattern_lab_intervals": SettingMeta(
        "패턴 백테스트 타임프레임",
        "패턴을 검증할 타임프레임 목록입니다. 예: 5m,15m,1h",
        "signals",
    ),
    "crypto_pattern_lab_context_limit": SettingMeta(
        "쥬 판단용 패턴 수",
        "바이낸스 쥬 프롬프트에 넣는 상위 패턴 scorecard 수입니다.",
        "signals",
        min_value=3,
        max_value=50,
        step=1,
    ),
```

- [ ] **Step 6: Create runner**

Create `src/tradecraft/runtime/crypto_pattern_lab_runner.py`:

```python
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.crypto_market_research_runner import parse_crypto_universe
from tradecraft.services.crypto_pattern_lab import (
    CryptoPatternLabConfig,
    CryptoPatternLabService,
    HermesKlineReader,
)

logger = logging.getLogger(__name__)


def _setting(settings: Any, name: str, default: Any = None) -> Any:
    return getattr(settings, name, default)


def build_crypto_pattern_lab_service(settings: AppSettings) -> CryptoPatternLabService:
    return CryptoPatternLabService(
        config=CryptoPatternLabConfig(
            db_path=settings.crypto_pattern_lab_db_path,
            enabled=settings.crypto_pattern_lab_enabled,
            strategy_paths=settings.crypto_pattern_lab_strategy_paths,
            freqtrade_data_paths=settings.crypto_pattern_lab_freqtrade_data_paths,
            max_symbols=settings.crypto_pattern_lab_max_symbols,
            intervals=settings.crypto_pattern_lab_intervals,
            lookback_bars=settings.crypto_pattern_lab_lookback_bars,
            context_limit=settings.crypto_pattern_lab_context_limit,
            retention_days=settings.crypto_pattern_lab_retention_days,
        ),
        kline_reader=HermesKlineReader(settings.crypto_market_research_db_path),
    )


async def run_crypto_pattern_lab_loop(
    *,
    settings: Any,
    service: Any | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> None:
    if not bool(_setting(settings, "crypto_pattern_lab_enabled", True)):
        logger.info("crypto pattern lab disabled")
        return
    resolved = service or build_crypto_pattern_lab_service(settings)
    state_path = Path(str(_setting(settings, "crypto_pattern_lab_state_path", ".runtime/crypto_pattern_lab.json")))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    interval = max(int(_setting(settings, "crypto_pattern_lab_interval_sec", 3600)), 60)
    once = bool(_setting(settings, "crypto_pattern_lab_once", False))
    cycle = 0
    while True:
        cycle += 1
        universe = parse_crypto_universe(
            _setting(settings, "crypto_market_research_universe", "BTCUSDT,ETHUSDT,SOLUSDT")
        )
        max_symbols = max(int(_setting(settings, "crypto_pattern_lab_max_symbols", 30)), 1)
        symbols = universe[:max_symbols]
        result = resolved.run_once(symbols=symbols)
        snapshot = {
            "service": "tradecraft-crypto-pattern-lab",
            "status": result.get("status", "ok"),
            "cycle": cycle,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols,
            "result": result,
            "service_status": resolved.status(),
        }
        state_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "crypto pattern lab cycle=%s status=%s patterns=%s backtests=%s",
            cycle,
            result.get("status"),
            result.get("pattern_count"),
            result.get("backtest_count"),
        )
        if once:
            return
        await sleep(float(interval))


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    asyncio.run(run_crypto_pattern_lab_loop(settings=AppSettings()))


if __name__ == "__main__":
    run()
```

- [ ] **Step 7: Add console script**

Add to `[project.scripts]` in `pyproject.toml`:

```toml
tradecraft-crypto-pattern-lab = "tradecraft.runtime.crypto_pattern_lab_runner:run"
```

- [ ] **Step 8: Run tests to verify pass**

Run:

```bash
pytest tests/test_config.py::test_crypto_pattern_lab_defaults tests/test_config.py::test_crypto_pattern_lab_short_env_aliases tests/test_crypto_pattern_lab_runner.py -q
```

Expected:

```text
... [100%]
```

---

### Task 6: Binance Jue Prompt Integration

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/runtime/binance_block_trader_runner.py`
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_binance_block_trader.py`
- Test: `tests/test_binance_block_trader_runner.py`

- [ ] **Step 1: Write failing Binance prompt test**

Append to `tests/test_binance_block_trader.py`:

```python
def test_manager_prompt_includes_crypto_pattern_context(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})

    class PatternProvider:
        def context_pack(self, *, symbols: list[str] | None = None, limit: int = 12) -> dict[str, Any]:
            return {
                "status": "ok",
                "scorecards": [
                    {
                        "symbol": "BTCUSDT",
                        "family": "ema_trend",
                        "direction": "long",
                        "expectancy_r": 0.22,
                        "trade_count": 18,
                        "score": 72.0,
                    }
                ],
            }

    trader = _trader(tmp_path, llm=llm)
    trader.crypto_pattern_provider = PatternProvider()

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BTCUSDT"}]))
    prompt = llm.calls[0]["payload"]

    assert "crypto_patterns" in prompt
    assert "crypto_patterns" in prompt["decision_inputs"]
    assert prompt["crypto_patterns"]["scorecards"][0]["family"] == "ema_trend"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_manager_prompt_includes_crypto_pattern_context -q
```

Expected:

```text
AssertionError: assert 'crypto_patterns' in ...
```

- [ ] **Step 3: Modify BinanceBlockTrader**

In `src/tradecraft/services/binance_block_trader.py`, change constructor signature:

```python
        crypto_alpha_provider: Any | None = None,
        quant_provider: Any | None = None,
        crypto_pattern_provider: Any | None = None,
        risk_sizer: Any | None = None,
```

Then assign:

```python
        self.crypto_pattern_provider = crypto_pattern_provider
```

In `run_manager_once`, after `crypto_quant = ...`, add:

```python
        crypto_patterns = self._crypto_pattern_context(symbols=symbols)
```

Add to prompt:

```python
            "crypto_patterns": crypto_patterns,
```

Add `"crypto_patterns"` to `decision_inputs`.

Add helper:

```python
    def _crypto_pattern_context(self, *, symbols: list[str]) -> dict[str, Any]:
        provider = getattr(self, "crypto_pattern_provider", None)
        if provider is None:
            return {"status": "missing", "scorecards": []}
        try:
            context_pack = getattr(provider, "context_pack")
            payload = context_pack(
                symbols=symbols,
                limit=max(int(self.config.quant_context_limit), 1),
            )
        except Exception as exc:
            logger.warning("binance crypto pattern context failed: %s", exc)
            return {"status": "error", "error_message": str(exc), "scorecards": []}
        return payload if isinstance(payload, dict) else {"status": "malformed", "scorecards": []}
```

- [ ] **Step 4: Wire runner and main app**

In `src/tradecraft/runtime/binance_block_trader_runner.py`, import:

```python
try:
    from tradecraft.services.crypto_pattern_lab import (
        CryptoPatternLabConfig,
        CryptoPatternLabService,
        HermesKlineReader,
    )
except Exception:
    CryptoPatternLabConfig = None  # type: ignore[assignment]
    CryptoPatternLabService = None  # type: ignore[assignment]
    HermesKlineReader = None  # type: ignore[assignment]
```

Create helper:

```python
def _build_crypto_pattern_service(settings: Any) -> Any | None:
    if not bool(_setting(settings, "crypto_pattern_lab_enabled", True)):
        return None
    if CryptoPatternLabConfig is None or CryptoPatternLabService is None or HermesKlineReader is None:
        return None
    return CryptoPatternLabService(
        config=CryptoPatternLabConfig(
            db_path=str(_setting(settings, "crypto_pattern_lab_db_path", ".runtime/crypto_pattern_lab.db")),
            enabled=bool(_setting(settings, "crypto_pattern_lab_enabled", True)),
            strategy_paths=str(_setting(settings, "crypto_pattern_lab_strategy_paths", "")),
            freqtrade_data_paths=str(_setting(settings, "crypto_pattern_lab_freqtrade_data_paths", "")),
            max_symbols=int(_setting(settings, "crypto_pattern_lab_max_symbols", 30)),
            intervals=str(_setting(settings, "crypto_pattern_lab_intervals", "5m,15m,1h")),
            lookback_bars=int(_setting(settings, "crypto_pattern_lab_lookback_bars", 500)),
            context_limit=int(_setting(settings, "crypto_pattern_lab_context_limit", 12)),
            retention_days=int(_setting(settings, "crypto_pattern_lab_retention_days", 90)),
        ),
        kline_reader=HermesKlineReader(str(_setting(settings, "crypto_market_research_db_path", ".runtime/crypto_market_research.db"))),
    )
```

In `_build_trader`, create:

```python
    crypto_patterns = _build_crypto_pattern_service(settings)
```

Pass to `BinanceBlockTrader`:

```python
        crypto_pattern_provider=crypto_patterns,
```

In `src/tradecraft/main.py`, create a global pattern service with the same config and pass it into the global `binance_block_trader`.

- [ ] **Step 5: Run focused tests**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_manager_prompt_includes_crypto_pattern_context tests/test_binance_block_trader_runner.py::test_build_trader_wires_crypto_research_provider -q
```

Expected:

```text
.. [100%]
```

---

### Task 7: Admin API And Frontend Pattern Board

**Files:**
- Modify: `src/tradecraft/main.py`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `tests/test_api_smoke.py`

- [ ] **Step 1: Write failing API test**

Append to `tests/test_api_smoke.py`:

```python
def test_binance_pattern_context_api_requires_admin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADECRAFT_ADMIN_TOKEN", "secret")
    monkeypatch.setattr(settings, "admin_token", "secret")
    monkeypatch.setattr(settings, "crypto_pattern_lab_db_path", str(tmp_path / "patterns.db"))

    from tradecraft.services.crypto_pattern_lab import CryptoPatternLabRepository

    repo = CryptoPatternLabRepository(tmp_path / "patterns.db")
    repo.save_patterns(
        [
            {
                "pattern_id": "p1",
                "source_id": "s1",
                "name": "EMA trend long",
                "family": "ema_trend",
                "direction": "long",
                "timeframe": "5m",
                "indicators": ["ema_fast", "ema_slow"],
                "expression": {},
                "risk_tags": [],
            }
        ]
    )

    with TestClient(app) as client:
        unauthorized = client.get("/api/binance/patterns/context")
        authorized = client.get(
            "/api/binance/patterns/context",
            headers={"Authorization": "Bearer secret"},
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["status"] == "ok"
    assert authorized.json()["patterns"][0]["family"] == "ema_trend"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_api_smoke.py::test_binance_pattern_context_api_requires_admin -q
```

Expected:

```text
404 Not Found
```

- [ ] **Step 3: Add API route**

In `src/tradecraft/main.py`, import:

```python
from tradecraft.services.crypto_pattern_lab import CryptoPatternLabRepository
```

Add route near Binance quant routes:

```python
@app.get("/api/binance/patterns/context")
def binance_pattern_context(
    request: Request,
    symbols: str = "",
    limit: int = 12,
) -> dict[str, Any]:
    require_admin_auth(request)
    repository = CryptoPatternLabRepository(settings.crypto_pattern_lab_db_path)
    clean_symbols = [
        item.strip().upper()
        for item in symbols.replace(";", ",").split(",")
        if item.strip()
    ]
    return repository.pattern_context(symbols=clean_symbols, limit=max(min(int(limit), 50), 1))
```

- [ ] **Step 4: Add frontend state and fetch**

In `src/tradecraft/web/static/app.js`, add to `state.binanceTrader`:

```javascript
patternContext: null,
patternError: "",
```

In `loadBinanceBlocks`, after quant fetch:

```javascript
  try {
    state.binanceTrader.patternContext = await getJSON("/binance/patterns/context?limit=12");
    state.binanceTrader.patternError = "";
  } catch (error) {
    state.binanceTrader.patternError = error.message || "패턴 컨텍스트를 불러오지 못했습니다.";
  }
```

Add renderer:

```javascript
function renderBinancePatternBoard() {
  const context = state.binanceTrader.patternContext || {};
  const rows = Array.isArray(context.scorecards) ? context.scorecards : [];
  const patterns = Array.isArray(context.patterns) ? context.patterns : [];
  if (state.binanceTrader.patternError) {
    return `<section class="binance-pattern-panel"><h3>전략 패턴 랩</h3><p class="muted">${escapeHTML(state.binanceTrader.patternError)}</p></section>`;
  }
  if (!rows.length && !patterns.length) {
    return `<section class="binance-pattern-panel"><h3>전략 패턴 랩</h3><p class="muted">아직 검증된 패턴 scorecard가 없습니다.</p></section>`;
  }
  const body = rows.slice(0, 12).map((row) => `
    <tr>
      <td>${escapeHTML(row.symbol || "-")}</td>
      <td>${escapeHTML(row.family || "-")}</td>
      <td>${escapeHTML(row.direction || "-")}</td>
      <td>${formatNumber(row.expectancy_r, 2)}R</td>
      <td>${formatNumber(row.win_rate * 100, 1)}%</td>
      <td>${formatNumber(row.trade_count, 0)}</td>
      <td>${formatNumber(row.score, 1)}</td>
    </tr>
  `).join("");
  return `
    <section class="binance-pattern-panel">
      <div class="panel-title-row">
        <h3>전략 패턴 랩</h3>
        <span class="soft-chip">${patterns.length} patterns</span>
      </div>
      <table class="pattern-table">
        <thead><tr><th>Symbol</th><th>Pattern</th><th>Side</th><th>Expectancy</th><th>Win</th><th>N</th><th>Score</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </section>
  `;
}
```

Call it in Binance tab render after `renderBinanceQuantBoard()`:

```javascript
${renderBinancePatternBoard()}
```

- [ ] **Step 5: Add CSS**

In `src/tradecraft/web/static/style.css`, add:

```css
.binance-pattern-panel {
  border: 1px solid var(--border-subtle);
  background: var(--surface-panel);
  border-radius: 8px;
  padding: 14px;
  overflow-x: auto;
}

.pattern-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 760px;
}

.pattern-table th,
.pattern-table td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--border-subtle);
  text-align: right;
  white-space: nowrap;
}

.pattern-table th:first-child,
.pattern-table td:first-child,
.pattern-table th:nth-child(2),
.pattern-table td:nth-child(2),
.pattern-table th:nth-child(3),
.pattern-table td:nth-child(3) {
  text-align: left;
}
```

- [ ] **Step 6: Run tests and JS check**

Run:

```bash
pytest tests/test_api_smoke.py::test_binance_pattern_context_api_requires_admin -q
node --check src/tradecraft/web/static/app.js
```

Expected:

```text
. [100%]
```

and `node --check` exits with code `0`.

---

### Task 8: Final Verification

**Files:**
- All files touched above.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
pytest tests/test_crypto_pattern_lab.py tests/test_crypto_pattern_lab_runner.py tests/test_binance_block_trader.py::test_manager_prompt_includes_crypto_pattern_context tests/test_binance_block_trader_runner.py::test_build_trader_wires_crypto_research_provider tests/test_api_smoke.py::test_binance_pattern_context_api_requires_admin tests/test_config.py::test_crypto_pattern_lab_defaults tests/test_config.py::test_crypto_pattern_lab_short_env_aliases -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Run existing related regressions**

Run:

```bash
pytest tests/test_crypto_quant.py tests/test_crypto_market_research.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_api_smoke.py::test_binance_quant_signals_api_requires_admin tests/test_config.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 3: Run static checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
ruff check src/tradecraft/services/crypto_pattern_lab.py src/tradecraft/runtime/crypto_pattern_lab_runner.py tests/test_crypto_pattern_lab.py tests/test_crypto_pattern_lab_runner.py
git diff --check -- src/tradecraft/services/crypto_pattern_lab.py src/tradecraft/runtime/crypto_pattern_lab_runner.py src/tradecraft/config.py src/tradecraft/services/settings_catalog.py src/tradecraft/main.py src/tradecraft/services/binance_block_trader.py src/tradecraft/runtime/binance_block_trader_runner.py src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css tests/test_crypto_pattern_lab.py tests/test_crypto_pattern_lab_runner.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_api_smoke.py tests/test_config.py pyproject.toml
```

Expected:

```text
All checks passed!
```

- [ ] **Step 4: Manual runtime smoke**

Run:

```bash
TRADECRAFT_CRYPTO_PATTERN_LAB_ONCE=true .venv/bin/tradecraft-crypto-pattern-lab
```

Expected:

```text
crypto pattern lab cycle=1 status=ok ...
```

Then restart these processes if they are running:

```bash
tmux kill-session -t hermes-crypto-pattern-lab 2>/dev/null || true
tmux kill-session -t hermes-binance-block-trader 2>/dev/null || true
tmux kill-session -t hermes-control 2>/dev/null || true
tmux new-session -d -s hermes-crypto-pattern-lab 'cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-crypto-pattern-lab 2>&1 | tee -a .runtime/crypto_pattern_lab.log'
tmux new-session -d -s hermes-binance-block-trader 'cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-binance-block-trader 2>&1 | tee -a .runtime/binance_block_trader.log'
tmux new-session -d -s hermes-control 'cd /Users/juhwan/hermes_v2 && .venv/bin/tradecraft-control 2>&1 | tee -a .runtime/control.log'
```

---

## Acceptance Criteria

- Freqtrade strategy files are never executed; only parsed with `ast`.
- Strategy files produce normalized pattern families such as `rsi_mean_reversion`, `ema_trend`, `bollinger_squeeze`, `macd_momentum`, and `volume_confirmation`.
- Existing Binance `crypto_klines` can be used as the primary backtest data source.
- Freqtrade JSON/JSONGZ OHLCV files can be imported without adding new dependencies.
- Pattern scorecards are stored in `.runtime/crypto_pattern_lab.db`.
- Binance Jue manager prompt includes compact `crypto_patterns`.
- UI shows a pattern board in the Binance tab.
- Pattern lab APIs require admin auth.
- Freqtrade is not used as the live execution engine.

## Self-Review

- Spec coverage: The plan covers time series access, Freqtrade strategy extraction, data import, pattern backtesting, DB storage, Jue prompt injection, API, UI, runner, settings, and tests.
- Placeholder scan: No `TBD`, `TODO`, or undefined later-only functions remain in the implementation snippets. Heavy Feather/Parquet support is deliberately out of v1 scope.
- Type consistency: `CryptoPatternLabConfig`, `CryptoPatternLabRepository`, `FreqtradeStrategyExtractor`, `FreqtradeOHLCVImporter`, `HermesKlineReader`, `PatternBacktestLab`, and `CryptoPatternLabService` are defined before they are used by runner/API integration tasks.
