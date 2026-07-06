# Crypto Market Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent crypto research layer that gives Binance Jue its own market-structure, derivatives, external-context, memory, and candidate evidence using GPT-5.3-Codex-Spark.

**Architecture:** Add `CryptoMarketResearchService` as a separate service backed by `.runtime/crypto_market_research.db`. Binance public/futures market data is collected and transformed into compact feature packets; GPT-5.3-Codex-Spark turns those packets plus memory/account/block context into symbol notes and trading candidates consumed by `BinanceBlockTrader`.

**Tech Stack:** Python 3.10, sqlite3, httpx through existing `BinanceAdapter`, FastAPI routes in `src/tradecraft/main.py`, existing `CodexNativeRuntime`, pytest, static frontend in `src/tradecraft/web/static`.

---

## File Structure

- Create `src/tradecraft/services/crypto_market_research.py`
  - Owns DB schema, feature calculation, Spark prompt building, external context normalization, latest candidate APIs.
- Create `src/tradecraft/runtime/crypto_market_research_runner.py`
  - Runs quote/kline/derivatives collection and Spark research on cadence.
- Modify `src/tradecraft/services/binance.py`
  - Add missing public methods for klines, 24h ticker, order book ticker, futures premium index, open interest, long/short ratios.
- Modify `src/tradecraft/services/binance_block_trader.py`
  - Accept `crypto_research_provider`; inject compact crypto candidates/context into manager prompt.
- Modify `src/tradecraft/runtime/binance_block_trader_runner.py`
  - Wire `CryptoMarketResearchService` into Binance trader.
- Modify `src/tradecraft/config.py`
  - Add env settings for DB path, intervals, Spark model, universe, external sources, max symbols.
- Modify `src/tradecraft/main.py`
  - Instantiate service and add protected `/api/crypto/research/*` routes.
- Modify `src/tradecraft/web/static/app.js`, `index.html`, `style.css`
  - Add Binance research panel in the Binance tab.
- Create `tests/test_crypto_market_research.py`
  - Unit tests for DB, feature calculations, prompt packets, Spark output parsing.
- Extend `tests/test_binance_adapter.py`, `tests/test_binance_block_trader.py`, `tests/test_config.py`, `tests/test_binance_trader_api.py`.

---

## Task 1: Config and DB Skeleton

**Files:**
- Modify: `src/tradecraft/config.py`
- Create: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_crypto_market_research.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing config and schema tests**

Add to `tests/test_config.py`:

```python
def test_crypto_market_research_settings_defaults() -> None:
    from tradecraft.config import AppSettings

    settings = AppSettings()

    assert settings.crypto_market_research_db_path == ".runtime/crypto_market_research.db"
    assert settings.crypto_market_research_llm_model == "gpt-5.3-codex-spark"
    assert settings.crypto_market_research_llm_reasoning_effort == "xhigh"
    assert settings.crypto_market_research_feature_interval_sec == 300
    assert settings.crypto_market_research_llm_interval_sec == 3600
```

Create `tests/test_crypto_market_research.py`:

```python
from pathlib import Path

from tradecraft.services.crypto_market_research import (
    CryptoMarketResearchConfig,
    CryptoMarketResearchService,
)


def test_crypto_research_db_schema_initializes(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )

    status = service.status()

    assert status["status"] == "ok"
    assert status["db_path"].endswith("crypto.db")
    assert status["snapshot_count"] == 0
    assert status["candidate_count"] == 0
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_config.py::test_crypto_market_research_settings_defaults tests/test_crypto_market_research.py::test_crypto_research_db_schema_initializes -q
```

Expected: fail because settings and service do not exist.

- [ ] **Step 3: Add config fields**

Add to `AppSettings` in `src/tradecraft/config.py` near Binance settings:

```python
crypto_market_research_enabled: bool = Field(
    default=True, alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_ENABLED"
)
crypto_market_research_once: bool = Field(
    default=False, alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_ONCE"
)
crypto_market_research_db_path: str = Field(
    default=".runtime/crypto_market_research.db",
    alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_DB_PATH",
)
crypto_market_research_state_path: str = Field(
    default=".runtime/crypto_market_research.json",
    alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_STATE_PATH",
)
crypto_market_research_universe: str = Field(
    default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT",
    alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_UNIVERSE",
)
crypto_market_research_max_symbols: int = Field(
    default=30, alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_MAX_SYMBOLS"
)
crypto_market_research_feature_interval_sec: int = Field(
    default=300, alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_FEATURE_INTERVAL_SEC"
)
crypto_market_research_llm_interval_sec: int = Field(
    default=3600, alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_LLM_INTERVAL_SEC"
)
crypto_market_research_llm_model: str = Field(
    default="gpt-5.3-codex-spark",
    alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_LLM_MODEL",
)
crypto_market_research_llm_reasoning_effort: str = Field(
    default="xhigh",
    alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_LLM_REASONING_EFFORT",
)
crypto_market_research_external_enabled: bool = Field(
    default=True, alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_EXTERNAL_ENABLED"
)
crypto_market_research_external_sources: str = Field(
    default="coingecko,defillama,fear_greed",
    alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_EXTERNAL_SOURCES",
)
```

- [ ] **Step 4: Add minimal service and schema**

Create `src/tradecraft/services/crypto_market_research.py`:

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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


@dataclass(slots=True)
class CryptoMarketResearchConfig:
    db_path: str = ".runtime/crypto_market_research.db"
    max_symbols: int = 30
    llm_model: str = "gpt-5.3-codex-spark"
    llm_reasoning_effort: str = "xhigh"


class CryptoMarketResearchRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS crypto_symbols (
                    symbol TEXT PRIMARY KEY,
                    base_asset TEXT NOT NULL DEFAULT '',
                    quote_asset TEXT NOT NULL DEFAULT '',
                    spot_enabled INTEGER NOT NULL DEFAULT 0,
                    futures_enabled INTEGER NOT NULL DEFAULT 0,
                    liquidity_tier TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS crypto_market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'spot',
                    price REAL NOT NULL DEFAULT 0,
                    quote_volume_usdt REAL NOT NULL DEFAULT 0,
                    change_pct_24h REAL NOT NULL DEFAULT 0,
                    spread_bps REAL NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    captured_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_market_snapshots_symbol
                    ON crypto_market_snapshots(symbol, market, captured_at DESC);
                CREATE TABLE IF NOT EXISTS crypto_klines (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT 'spot',
                    interval TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    open REAL NOT NULL DEFAULT 0,
                    high REAL NOT NULL DEFAULT 0,
                    low REAL NOT NULL DEFAULT 0,
                    close REAL NOT NULL DEFAULT 0,
                    volume REAL NOT NULL DEFAULT 0,
                    quote_volume REAL NOT NULL DEFAULT 0,
                    close_time INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(symbol, market, interval, open_time)
                );
                CREATE TABLE IF NOT EXISTS crypto_derivatives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    mark_price REAL NOT NULL DEFAULT 0,
                    index_price REAL NOT NULL DEFAULT 0,
                    funding_rate REAL NOT NULL DEFAULT 0,
                    next_funding_time INTEGER NOT NULL DEFAULT 0,
                    open_interest REAL NOT NULL DEFAULT 0,
                    long_short_ratio REAL NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    captured_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_crypto_derivatives_symbol
                    ON crypto_derivatives(symbol, captured_at DESC);
                CREATE TABLE IF NOT EXISTS crypto_features (
                    symbol TEXT PRIMARY KEY,
                    feature_json TEXT NOT NULL DEFAULT '{}',
                    score REAL NOT NULL DEFAULT 0,
                    regime TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS crypto_external_context (
                    source_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY(source_id, key)
                );
                CREATE TABLE IF NOT EXISTS crypto_research_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'llm',
                    model TEXT NOT NULL DEFAULT '',
                    prompt_json TEXT NOT NULL DEFAULT '{}',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS crypto_symbol_notes (
                    symbol TEXT PRIMARY KEY,
                    stance TEXT NOT NULL DEFAULT '',
                    horizon TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    summary_md TEXT NOT NULL DEFAULT '',
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    risks_json TEXT NOT NULL DEFAULT '[]',
                    triggers_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS crypto_candidates (
                    symbol TEXT PRIMARY KEY,
                    market TEXT NOT NULL DEFAULT 'spot',
                    stance TEXT NOT NULL DEFAULT '',
                    horizon TEXT NOT NULL DEFAULT '',
                    score REAL NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    reason_md TEXT NOT NULL DEFAULT '',
                    block_template_json TEXT NOT NULL DEFAULT '{}',
                    source_run_id INTEGER,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            snapshot_count = int(conn.execute("SELECT COUNT(*) FROM crypto_market_snapshots").fetchone()[0])
            candidate_count = int(conn.execute("SELECT COUNT(*) FROM crypto_candidates").fetchone()[0])
        return {
            "status": "ok",
            "db_path": str(self.path),
            "snapshot_count": snapshot_count,
            "candidate_count": candidate_count,
        }


class CryptoMarketResearchService:
    def __init__(
        self,
        *,
        config: CryptoMarketResearchConfig,
        binance: Any | None = None,
        codex_runtime: Any | None = None,
        memory_provider: Any | None = None,
    ) -> None:
        self.config = config
        self.repository = CryptoMarketResearchRepository(config.db_path)
        self.binance = binance
        self.codex_runtime = codex_runtime
        self.memory_provider = memory_provider

    def status(self) -> dict[str, Any]:
        return {
            **self.repository.status(),
            "model": self.config.llm_model,
            "reasoning_effort": self.config.llm_reasoning_effort,
        }
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_config.py::test_crypto_market_research_settings_defaults tests/test_crypto_market_research.py::test_crypto_research_db_schema_initializes -q
```

Expected: pass.

---

## Task 2: Binance Market Data Adapter Expansion

**Files:**
- Modify: `src/tradecraft/services/binance.py`
- Test: `tests/test_binance_adapter.py`

- [ ] **Step 1: Write failing adapter tests**

Add to `tests/test_binance_adapter.py`:

```python
def test_binance_public_market_research_helpers(monkeypatch) -> None:
    from tradecraft.services.binance import BinanceAdapter, BinanceConfig

    calls = []

    async def fake_public_get(self, market, path, params):
        calls.append((market, path, params))
        if path.endswith("/ticker/24hr"):
            return {"symbol": "BTCUSDT", "priceChangePercent": "2.5", "quoteVolume": "1000000"}
        if path.endswith("/bookTicker"):
            return {"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "100.1"}
        if path.endswith("/klines"):
            return [[1, "100", "110", "90", "105", "10", 2, "1050"]]
        if path.endswith("/premiumIndex"):
            return {
                "symbol": "BTCUSDT",
                "markPrice": "101",
                "indexPrice": "100",
                "lastFundingRate": "0.0001",
                "nextFundingTime": 123,
            }
        if path.endswith("/openInterest"):
            return {"symbol": "BTCUSDT", "openInterest": "12345"}
        return {}

    monkeypatch.setattr(BinanceAdapter, "_public_get", fake_public_get)
    adapter = BinanceAdapter(BinanceConfig())

    assert adapter._to_float("1,234") == 1234.0
    assert asyncio.run(adapter.fetch_24h_ticker("BTCUSDT"))["quote_volume"] == 1000000.0
    assert asyncio.run(adapter.fetch_book_ticker("BTCUSDT"))["spread_bps"] == pytest.approx(9.995)
    assert asyncio.run(adapter.fetch_klines("BTCUSDT", interval="1m", limit=1))[0]["close"] == 105.0
    assert asyncio.run(adapter.fetch_futures_premium_index("BTCUSDT"))["funding_rate"] == 0.0001
    assert asyncio.run(adapter.fetch_futures_open_interest("BTCUSDT"))["open_interest"] == 12345.0
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_binance_adapter.py::test_binance_public_market_research_helpers -q
```

Expected: fail because helper methods do not exist.

- [ ] **Step 3: Implement helper methods**

Add methods to `BinanceAdapter` after `fetch_futures_quote`:

```python
    async def fetch_24h_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
        market_key = self._normalize_market(market)
        path = "/api/v3/ticker/24hr" if market_key == "spot" else "/fapi/v1/ticker/24hr"
        payload = await self._public_get(market_key, path, {"symbol": self._normalize_symbol(symbol)})
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance ticker 24hr malformed")
        return {
            "symbol": self._normalize_symbol(payload.get("symbol")),
            "market": market_key,
            "change_pct_24h": self._to_float(payload.get("priceChangePercent")),
            "quote_volume": self._to_float(payload.get("quoteVolume")),
            "raw": payload,
        }

    async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
        market_key = self._normalize_market(market)
        path = "/api/v3/ticker/bookTicker" if market_key == "spot" else "/fapi/v1/ticker/bookTicker"
        payload = await self._public_get(market_key, path, {"symbol": self._normalize_symbol(symbol)})
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance book ticker malformed")
        bid = self._to_float(payload.get("bidPrice"))
        ask = self._to_float(payload.get("askPrice"))
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
        spread_bps = ((ask - bid) / mid * 10000.0) if mid > 0 else 0.0
        return {
            "symbol": self._normalize_symbol(payload.get("symbol")),
            "market": market_key,
            "bid": bid,
            "ask": ask,
            "spread_bps": spread_bps,
            "raw": payload,
        }

    async def fetch_klines(
        self,
        symbol: str,
        *,
        market: str = "spot",
        interval: str = "1m",
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        market_key = self._normalize_market(market)
        path = "/api/v3/klines" if market_key == "spot" else "/fapi/v1/klines"
        payload = await self._public_get(
            market_key,
            path,
            {
                "symbol": self._normalize_symbol(symbol),
                "interval": interval,
                "limit": max(min(int(limit), 1000), 1),
            },
        )
        if not isinstance(payload, list):
            raise BinanceAPIError("binance klines malformed")
        rows = []
        for item in payload:
            if not isinstance(item, list) or len(item) < 7:
                continue
            rows.append(
                {
                    "open_time": int(item[0]),
                    "open": self._to_float(item[1]),
                    "high": self._to_float(item[2]),
                    "low": self._to_float(item[3]),
                    "close": self._to_float(item[4]),
                    "volume": self._to_float(item[5]),
                    "close_time": int(item[6]),
                    "quote_volume": self._to_float(item[7]) if len(item) > 7 else 0.0,
                    "raw": item,
                }
            )
        return rows

    async def fetch_futures_premium_index(self, symbol: str) -> dict[str, Any]:
        payload = await self._public_get("futures", "/fapi/v1/premiumIndex", {"symbol": self._normalize_symbol(symbol)})
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance premium index malformed")
        return {
            "symbol": self._normalize_symbol(payload.get("symbol")),
            "mark_price": self._to_float(payload.get("markPrice")),
            "index_price": self._to_float(payload.get("indexPrice")),
            "funding_rate": self._to_float(payload.get("lastFundingRate")),
            "next_funding_time": int(self._to_float(payload.get("nextFundingTime"))),
            "raw": payload,
        }

    async def fetch_futures_open_interest(self, symbol: str) -> dict[str, Any]:
        payload = await self._public_get("futures", "/fapi/v1/openInterest", {"symbol": self._normalize_symbol(symbol)})
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance open interest malformed")
        return {
            "symbol": self._normalize_symbol(payload.get("symbol")),
            "open_interest": self._to_float(payload.get("openInterest")),
            "raw": payload,
        }
```

- [ ] **Step 4: Run adapter tests**

Run:

```bash
pytest tests/test_binance_adapter.py::test_binance_public_market_research_helpers -q
```

Expected: pass.

---

## Task 3: Phase 1 Market Structure Collection and Features

**Files:**
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_crypto_market_research.py`

- [ ] **Step 1: Write failing feature tests**

Add:

```python
class _FakeBinance:
    async def fetch_24h_ticker(self, symbol: str, *, market: str = "spot") -> dict:
        return {"symbol": symbol, "market": market, "change_pct_24h": 3.0, "quote_volume": 2_000_000, "raw": {}}

    async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict:
        return {"symbol": symbol, "market": market, "bid": 99.9, "ask": 100.1, "spread_bps": 20.0, "raw": {}}

    async def fetch_klines(self, symbol: str, *, market: str = "spot", interval: str = "1m", limit: int = 120) -> list[dict]:
        return [
            {"open_time": idx, "open": 100 + idx, "high": 101 + idx, "low": 99 + idx, "close": 100 + idx, "volume": 10 + idx, "quote_volume": 1000 + idx, "close_time": idx + 1, "raw": []}
            for idx in range(20)
        ]

    async def fetch_futures_premium_index(self, symbol: str) -> dict:
        return {"symbol": symbol, "mark_price": 120, "index_price": 119, "funding_rate": 0.0003, "next_funding_time": 123, "raw": {}}

    async def fetch_futures_open_interest(self, symbol: str) -> dict:
        return {"symbol": symbol, "open_interest": 12345, "raw": {}}


def test_collect_phase1_market_structure_builds_features(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        binance=_FakeBinance(),
    )

    result = asyncio.run(service.collect_market_structure(["BTCUSDT"]))
    context = service.latest_context(limit=5)

    assert result["status"] == "ok"
    assert result["collected_count"] == 1
    assert context["items"][0]["symbol"] == "BTCUSDT"
    assert context["items"][0]["features"]["trend_1m"] == "up"
    assert context["items"][0]["features"]["quote_volume_usdt"] == 2_000_000
    assert context["items"][0]["features"]["funding_rate"] == 0.0003
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_crypto_market_research.py::test_collect_phase1_market_structure_builds_features -q
```

Expected: fail because collection methods do not exist.

- [ ] **Step 3: Implement collection methods**

Add repository methods `upsert_symbol`, `save_market_snapshot`, `save_klines`, `save_derivatives`, `upsert_features`, `latest_features`.

Add service methods:

```python
    async def collect_market_structure(self, symbols: list[str]) -> dict[str, Any]:
        collected = []
        errors = []
        for raw_symbol in symbols[: max(int(self.config.max_symbols), 1)]:
            symbol = str(raw_symbol or "").upper().strip()
            if not symbol:
                continue
            try:
                snapshot = await self._collect_symbol_market(symbol)
                features = self._build_features(snapshot)
                self.repository.upsert_features(symbol, features)
                collected.append({"symbol": symbol, "features": features})
            except Exception as exc:
                errors.append({"symbol": symbol, "error_message": str(exc)})
        return {
            "status": "partial" if errors and collected else "error" if errors else "ok",
            "collected_count": len(collected),
            "error_count": len(errors),
            "errors": errors[:10],
        }

    async def _collect_symbol_market(self, symbol: str) -> dict[str, Any]:
        if self.binance is None:
            raise RuntimeError("binance adapter missing")
        ticker = await self.binance.fetch_24h_ticker(symbol, market="spot")
        book = await self.binance.fetch_book_ticker(symbol, market="spot")
        klines_1m = await self.binance.fetch_klines(symbol, market="spot", interval="1m", limit=120)
        premium = await self.binance.fetch_futures_premium_index(symbol)
        open_interest = await self.binance.fetch_futures_open_interest(symbol)
        captured_at = utc_now_iso()
        self.repository.save_market_snapshot(symbol=symbol, market="spot", ticker=ticker, book=book, captured_at=captured_at)
        self.repository.save_klines(symbol=symbol, market="spot", interval="1m", rows=klines_1m)
        self.repository.save_derivatives(symbol=symbol, premium=premium, open_interest=open_interest, captured_at=captured_at)
        return {
            "symbol": symbol,
            "ticker": ticker,
            "book": book,
            "klines_1m": klines_1m,
            "premium": premium,
            "open_interest": open_interest,
        }

    def _build_features(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        klines = list(snapshot.get("klines_1m") or [])
        first_close = float((klines[0] if klines else {}).get("close") or 0)
        last_close = float((klines[-1] if klines else {}).get("close") or 0)
        trend = "up" if last_close > first_close else "down" if last_close < first_close else "flat"
        ticker = snapshot.get("ticker") if isinstance(snapshot.get("ticker"), dict) else {}
        book = snapshot.get("book") if isinstance(snapshot.get("book"), dict) else {}
        premium = snapshot.get("premium") if isinstance(snapshot.get("premium"), dict) else {}
        open_interest = snapshot.get("open_interest") if isinstance(snapshot.get("open_interest"), dict) else {}
        return {
            "symbol": snapshot.get("symbol"),
            "trend_1m": trend,
            "price": last_close,
            "change_pct_24h": float(ticker.get("change_pct_24h") or 0),
            "quote_volume_usdt": float(ticker.get("quote_volume") or 0),
            "spread_bps": float(book.get("spread_bps") or 0),
            "funding_rate": float(premium.get("funding_rate") or 0),
            "mark_index_basis_pct": (
                (float(premium.get("mark_price") or 0) - float(premium.get("index_price") or 0))
                / float(premium.get("index_price") or 1)
                * 100
            ),
            "open_interest": float(open_interest.get("open_interest") or 0),
        }
```

- [ ] **Step 4: Run feature tests**

Run:

```bash
pytest tests/test_crypto_market_research.py::test_collect_phase1_market_structure_builds_features -q
```

Expected: pass.

---

## Task 4: Phase 2 External Context Collectors

**Files:**
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_crypto_market_research.py`

- [ ] **Step 1: Write failing external-context tests**

Add:

```python
def test_external_context_is_normalized_and_limited(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )

    result = service.save_external_context(
        source_id="coingecko",
        key="BTC",
        payload={
            "market_cap_rank": 1,
            "developer_score": 92,
            "community_score": 81,
            "description": "A" * 5000,
        },
    )
    context = service.external_context(keys=["BTC"], limit=3)

    assert result["status"] == "ok"
    assert context["items"][0]["source_id"] == "coingecko"
    assert context["items"][0]["key"] == "BTC"
    assert len(context["items"][0]["payload"]["description"]) <= 1200
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_crypto_market_research.py::test_external_context_is_normalized_and_limited -q
```

Expected: fail because external context methods do not exist.

- [ ] **Step 3: Implement external context storage**

Add to service:

```python
    def save_external_context(self, *, source_id: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean_payload = self._compact_external_payload(payload)
        self.repository.upsert_external_context(
            source_id=source_id,
            key=key,
            payload=clean_payload,
            captured_at=utc_now_iso(),
        )
        return {"status": "ok", "source_id": source_id, "key": key}

    def external_context(self, *, keys: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
        return {"status": "ok", "items": self.repository.list_external_context(keys=keys or [], limit=limit)}

    @staticmethod
    def _compact_external_payload(payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {}
        for key, value in payload.items():
            if isinstance(value, str):
                allowed[key] = value[:1200]
            elif isinstance(value, (int, float, bool)) or value is None:
                allowed[key] = value
            elif isinstance(value, list):
                allowed[key] = value[:20]
            elif isinstance(value, dict):
                allowed[key] = {str(k): v for k, v in list(value.items())[:20]}
        return allowed
```

Add repository `upsert_external_context` and `list_external_context`.

- [ ] **Step 4: Run external context tests**

Run:

```bash
pytest tests/test_crypto_market_research.py::test_external_context_is_normalized_and_limited -q
```

Expected: pass.

---

## Task 5: Spark Research Runs and Candidate Generation

**Files:**
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_crypto_market_research.py`

- [ ] **Step 1: Write failing Spark research test**

Add:

```python
class _FakeSpark:
    ready = True
    resolved_model = "gpt-5.3-codex-spark"

    def __init__(self) -> None:
        self.calls = []

    async def complete_json(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        return {
            "symbol_notes": [
                {
                    "symbol": "BTCUSDT",
                    "stance": "long_watch",
                    "horizon": "swing",
                    "confidence": 0.72,
                    "summary_md": "BTC는 유동성과 추세가 우세하다.",
                    "reasons": ["1m 추세 상승", "거래대금 충분"],
                    "risks": ["funding 과열"],
                    "triggers": ["전고점 돌파"],
                }
            ],
            "candidates": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "stance": "long_watch",
                    "horizon": "swing",
                    "score": 78,
                    "confidence": 0.72,
                    "reason_md": "추세와 유동성 우세",
                    "block_template": {"target_price": 110, "stop_price": 95},
                }
            ],
        }


def test_run_spark_research_persists_notes_and_candidates(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        codex_runtime=_FakeSpark(),
    )
    service.repository.upsert_features("BTCUSDT", {"symbol": "BTCUSDT", "trend_1m": "up", "quote_volume_usdt": 2_000_000})

    result = asyncio.run(service.run_research_once(symbols=["BTCUSDT"]))
    context = service.latest_context(limit=5)

    assert result["status"] == "ok"
    assert result["candidate_count"] == 1
    assert context["candidates"][0]["symbol"] == "BTCUSDT"
    assert context["symbol_notes"]["BTCUSDT"]["stance"] == "long_watch"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_crypto_market_research.py::test_run_spark_research_persists_notes_and_candidates -q
```

Expected: fail because Spark run methods do not exist.

- [ ] **Step 3: Implement prompt and persistence**

Add:

```python
    async def run_research_once(self, *, symbols: list[str] | None = None) -> dict[str, Any]:
        packet = self._research_packet(symbols=symbols or [])
        prompt = self._build_research_prompt(packet)
        output = await self._complete_research_json(prompt)
        run_id = self.repository.save_research_run(
            status="ok",
            mode="llm",
            model=str(getattr(self.codex_runtime, "resolved_model", self.config.llm_model)),
            prompt=prompt,
            response=output,
            error_message="",
        )
        note_count = self._save_symbol_notes(output.get("symbol_notes") or [])
        candidate_count = self._save_candidates(output.get("candidates") or [], source_run_id=run_id)
        return {"status": "ok", "run_id": run_id, "note_count": note_count, "candidate_count": candidate_count}

    def _build_research_prompt(self, packet: dict[str, Any]) -> dict[str, Any]:
        return {
            "system": "너는 HERMES 바이낸스 담당 쥬다. 크립토 시장구조와 파생지표를 블록 운용 근거로 압축한다.",
            "task": "Return JSON only. Build crypto symbol notes and candidates.",
            "model_role": "crypto_market_research_analyst",
            "scope": "binance",
            "inputs": packet,
            "output_schema": {
                "symbol_notes": [{"symbol": "BTCUSDT", "stance": "long_watch|short_watch|hold|avoid", "horizon": "scalp|intraday|swing|core", "confidence": 0.0, "summary_md": "string", "reasons": [], "risks": [], "triggers": []}],
                "candidates": [{"symbol": "BTCUSDT", "market": "spot|futures", "stance": "long_watch|short_watch|hold|avoid", "horizon": "scalp|intraday|swing|core", "score": 0, "confidence": 0.0, "reason_md": "string", "block_template": {}}],
            },
        }

    async def _complete_research_json(self, prompt: dict[str, Any]) -> dict[str, Any]:
        if self.codex_runtime is None:
            return {"symbol_notes": [], "candidates": []}
        complete_json = getattr(self.codex_runtime, "complete_json", None)
        if complete_json is None:
            return {"symbol_notes": [], "candidates": []}
        payload = await complete_json(
            prompt,
            model=self.config.llm_model,
            reasoning_effort=self.config.llm_reasoning_effort,
        )
        return payload if isinstance(payload, dict) else {"symbol_notes": [], "candidates": []}
```

Add repository methods for `save_research_run`, `upsert_symbol_note`, `upsert_candidate`, `latest_context`.

- [ ] **Step 4: Run Spark tests**

Run:

```bash
pytest tests/test_crypto_market_research.py::test_run_spark_research_persists_notes_and_candidates -q
```

Expected: pass.

---

## Task 6: Binance Manager Integration

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/runtime/binance_block_trader_runner.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing manager integration test**

Add to `tests/test_binance_block_trader.py`:

```python
def test_manager_prompt_includes_crypto_research_context(tmp_path: Path) -> None:
    class CryptoResearch:
        def latest_context(self, *, symbols=None, limit=10):
            return {
                "status": "ok",
                "items": [{"symbol": "BTCUSDT", "features": {"trend_1m": "up"}}],
                "candidates": [{"symbol": "BTCUSDT", "market": "spot", "stance": "long_watch", "score": 78}],
                "symbol_notes": {"BTCUSDT": {"summary_md": "BTC research note"}},
            }

    llm = _FakeLLM({"create_blocks": []})
    trader = _trader(tmp_path, llm=llm)
    trader.crypto_research_provider = CryptoResearch()

    result = asyncio.run(trader.run_manager_once(candidates=[]))
    prompt = llm.calls[0]["payload"]

    assert result["status"] == "ok"
    assert prompt["crypto_research"]["candidates"][0]["score"] == 78
    assert "crypto_research" in prompt["decision_inputs"]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_manager_prompt_includes_crypto_research_context -q
```

Expected: fail because trader does not expose crypto research provider.

- [ ] **Step 3: Add provider wiring**

Modify `BinanceBlockTrader.__init__`:

```python
        crypto_research_provider: Any | None = None,
```

Set:

```python
        self.crypto_research_provider = crypto_research_provider
```

Add method:

```python
    def _crypto_research_context(self, *, symbols: list[str]) -> dict[str, Any]:
        provider = self.crypto_research_provider
        if provider is None:
            return {"status": "missing"}
        try:
            return provider.latest_context(symbols=symbols, limit=12)
        except TypeError:
            return provider.latest_context(limit=12)
        except Exception as exc:
            return {"status": "error", "error_message": str(exc)}
```

In `run_manager_once`, before prompt:

```python
        crypto_research = self._crypto_research_context(symbols=symbols)
```

In prompt:

```python
            "crypto_research": crypto_research,
            "decision_inputs": ["account", "memory", "crypto_research", "candidates", "blocks"],
```

- [ ] **Step 4: Wire runtime builder**

In `src/tradecraft/runtime/binance_block_trader_runner.py`, instantiate `CryptoMarketResearchService` with `BinanceAdapter`, Spark `CodexNativeRuntime`, and memory provider. Pass it to `BinanceBlockTrader(crypto_research_provider=crypto_research)`.

- [ ] **Step 5: Run integration test**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_manager_prompt_includes_crypto_research_context -q
```

Expected: pass.

---

## Task 7: API and Runner

**Files:**
- Create: `src/tradecraft/runtime/crypto_market_research_runner.py`
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_binance_trader_api.py`

- [ ] **Step 1: Write failing API tests**

Add to `tests/test_binance_trader_api.py`:

```python
def test_crypto_research_status_requires_admin(client):
    response = client.get("/api/crypto/research/status")
    assert response.status_code in {401, 403}


def test_crypto_research_status_with_admin(client, admin_headers):
    response = client.get("/api/crypto/research/status", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_binance_trader_api.py::test_crypto_research_status_requires_admin tests/test_binance_trader_api.py::test_crypto_research_status_with_admin -q
```

Expected: fail because routes do not exist.

- [ ] **Step 3: Add main app service and routes**

In `main.py`, instantiate `crypto_market_research_service` after `binance_manager_codex_runtime`.

Add routes:

```python
@app.get("/api/crypto/research/status")
async def crypto_research_status(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    return crypto_market_research_service.status()


@app.get("/api/crypto/research/context")
async def crypto_research_context(
    limit: int = 20,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    return crypto_market_research_service.latest_context(limit=max(min(int(limit), 100), 1))


@app.post("/api/crypto/research/collect")
async def crypto_research_collect(
    payload: dict[str, Any] | None = None,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    symbols = [str(row) for row in list((payload or {}).get("symbols") or [])]
    return await crypto_market_research_service.collect_market_structure(symbols)


@app.post("/api/crypto/research/run-once")
async def crypto_research_run_once(
    payload: dict[str, Any] | None = None,
    _: None = Depends(require_admin_auth),
) -> dict[str, Any]:
    symbols = [str(row) for row in list((payload or {}).get("symbols") or [])]
    return await crypto_market_research_service.run_research_once(symbols=symbols)
```

- [ ] **Step 4: Create runner**

Create `src/tradecraft/runtime/crypto_market_research_runner.py` with loop:

```python
async def run_crypto_market_research_loop(*, settings: AppSettings, service: CryptoMarketResearchService | None = None, sleep: SleepFn = asyncio.sleep) -> None:
    resolved = service or build_crypto_market_research_service(settings)
    store = RuntimeStateStore(settings.crypto_market_research_state_path)
    feature_interval = max(int(settings.crypto_market_research_feature_interval_sec), 60)
    llm_interval = max(int(settings.crypto_market_research_llm_interval_sec), 300)
    last_llm_at = 0.0
    while True:
        symbols = parse_crypto_universe(settings.crypto_market_research_universe)[: settings.crypto_market_research_max_symbols]
        collect = await resolved.collect_market_structure(symbols)
        now = datetime.now(timezone.utc).timestamp()
        research = {"status": "skipped", "reason": "cadence"}
        if now - last_llm_at >= llm_interval:
            research = await resolved.run_research_once(symbols=symbols)
            last_llm_at = now
        store.write_snapshot({"service": "tradecraft-crypto-market-research", "status": collect.get("status"), "collect": collect, "research": research, "updated_at": datetime.now(timezone.utc).isoformat()})
        if settings.crypto_market_research_once:
            return
        await sleep(float(feature_interval))
```

- [ ] **Step 5: Run API tests**

Run:

```bash
pytest tests/test_binance_trader_api.py::test_crypto_research_status_requires_admin tests/test_binance_trader_api.py::test_crypto_research_status_with_admin -q
```

Expected: pass.

---

## Task 8: UI Panel in Binance Tab

**Files:**
- Modify: `src/tradecraft/web/static/index.html`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `node --check src/tradecraft/web/static/app.js`

- [ ] **Step 1: Add frontend state and fetch**

In `app.js`, add to `state`:

```javascript
cryptoResearch: {
  status: null,
  context: null,
  loading: false,
  error: "",
},
```

Add:

```javascript
async function loadCryptoResearch() {
  state.cryptoResearch.loading = true;
  state.cryptoResearch.error = "";
  try {
    const [status, context] = await Promise.all([
      apiFetch("/api/crypto/research/status"),
      apiFetch("/api/crypto/research/context?limit=12"),
    ]);
    state.cryptoResearch.status = status;
    state.cryptoResearch.context = context;
  } catch (error) {
    state.cryptoResearch.error = formatError(error);
  } finally {
    state.cryptoResearch.loading = false;
    renderCryptoResearchPanel();
  }
}
```

- [ ] **Step 2: Add render function**

Add:

```javascript
function renderCryptoResearchPanel() {
  const root = document.querySelector("[data-crypto-research-panel]");
  if (!root) return;
  const context = state.cryptoResearch.context || {};
  const candidates = Array.isArray(context.candidates) ? context.candidates : [];
  const notes = context.symbol_notes || {};
  root.innerHTML = `
    <div class="section-header compact">
      <div>
        <p class="eyebrow">Crypto Market Research</p>
        <h3>바이낸스 리서치</h3>
      </div>
      <button class="btn subtle" data-action="refresh-crypto-research">갱신</button>
    </div>
    <div class="crypto-research-grid">
      ${candidates.map((row) => `
        <article class="crypto-research-card">
          <div class="card-row">
            <strong>${escapeHtml(row.symbol || "-")}</strong>
            <span class="status-chip">${escapeHtml(row.stance || "-")}</span>
          </div>
          <div class="metric-row">
            <span>점수 ${Number(row.score || 0).toFixed(0)}</span>
            <span>${escapeHtml(row.market || "spot")}</span>
          </div>
          <p>${escapeHtml(row.reason_md || (notes[row.symbol] || {}).summary_md || "")}</p>
        </article>
      `).join("") || `<div class="empty-state">아직 생성된 크립토 후보가 없습니다.</div>`}
    </div>
  `;
}
```

- [ ] **Step 3: Add panel container**

In the Binance tab section of `index.html`, add:

```html
<section class="panel research-panel" data-crypto-research-panel></section>
```

- [ ] **Step 4: Add CSS**

In `style.css`:

```css
.crypto-research-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.crypto-research-card {
  border: 1px solid var(--border-subtle);
  background: var(--surface-1);
  border-radius: 8px;
  padding: 12px;
  min-width: 0;
}

.crypto-research-card p {
  color: var(--text-muted);
  line-height: 1.5;
  margin: 8px 0 0;
}
```

- [ ] **Step 5: Run frontend syntax check**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no output and exit code 0.

---

## Task 9: Full Verification

**Files:**
- All touched files

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_crypto_market_research.py tests/test_binance_adapter.py tests/test_binance_block_trader.py tests/test_binance_trader_api.py tests/test_config.py -q
```

Expected: all pass.

- [ ] **Step 2: Run API smoke**

Run:

```bash
pytest tests/test_api_smoke.py -q
```

Expected: all pass.

- [ ] **Step 3: Run lint and static checks**

Run:

```bash
ruff check src/tradecraft/services/crypto_market_research.py src/tradecraft/runtime/crypto_market_research_runner.py src/tradecraft/services/binance.py src/tradecraft/services/binance_block_trader.py src/tradecraft/main.py tests/test_crypto_market_research.py tests/test_binance_adapter.py tests/test_binance_block_trader.py tests/test_binance_trader_api.py tests/test_config.py
node --check src/tradecraft/web/static/app.js
git diff --check
```

Expected: no failures.

- [ ] **Step 4: Restart local services**

Run:

```bash
tmux kill-session -t hermes-control 2>/dev/null || true
tmux new-session -d -s hermes-control -c /Users/juhwan/hermes_v2 'TRADECRAFT_PORT=18080 .venv/bin/tradecraft-control > .runtime/control.log 2>&1'
```

Expected:

```bash
curl -sS http://127.0.0.1:18080/api/health
```

returns JSON with `"status":"ok"`.

---

## Self-Review

- Spec coverage: phase 1 market data, phase 2 external context, Spark research, Binance manager integration, API, UI, runner, tests are covered.
- Placeholder scan: the plan contains no open-ended implementation placeholders.
- Type consistency: service names, config names, route names, test names, and prompt fields are consistent across tasks.
- Scope check: this is a large but cohesive feature. It should be implemented with subagent-driven development, with Task 1-5 owned by backend/data workers and Task 8 owned by a frontend worker.
