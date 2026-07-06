# Binance Jue Quant Engine v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-class quant layer for Binance Jue so spot/futures decisions are driven by compact directional scores, volatility-aware features, and post-trade outcome learning.

**Architecture:** Add a focused `crypto_quant` service that reads Binance market data already collected by `CryptoMarketResearchService`, produces long/short/no-trade signal packets, stores them in SQLite, and injects them into the Binance block manager prompt. The LLM remains the block manager, while safety gates and rule execution remain deterministic; quant signals are decision evidence and sizing context, not a new hard-filter system.

**Tech Stack:** Python 3.10+, SQLite, pytest, existing Binance adapter, existing static frontend (`index.html`, `app.js`, `style.css`), existing FastAPI route wiring.

---

## Current State

- `src/tradecraft/services/crypto_market_research.py` already collects 24h ticker, book spread, klines, funding, basis, open interest, multi-timeframe trend, squeeze risk, entry quality, and a simple heuristic score.
- `src/tradecraft/services/binance_risk.py` sizes orders with account risk %, symbol exposure, reward/risk, and price-direction validation.
- `src/tradecraft/services/binance_block_trader.py` sends the LLM a prompt containing account, memory, crypto research, crypto alpha, performance, candidates, and blocks.
- `.runtime/crypto_market_research.db` currently stores about 50 symbol feature rows.
- `.runtime/binance_blocks.db` has sparse Binance performance history; recent BNB short reflections showed weak directionality, so the quant layer must explicitly distinguish long, short, and no-trade.

## File Structure

- Create `src/tradecraft/services/crypto_quant.py`
  - Owns quant signal calculations, signal storage, compact context output, and outcome labeling helpers.
- Modify `src/tradecraft/services/crypto_market_research.py`
  - Calls the quant service after market snapshots are stored and exposes quant context through `latest_context`.
- Modify `src/tradecraft/services/binance_block_trader.py`
  - Adds `crypto_quant` to the manager prompt and performance feedback loop.
- Modify `src/tradecraft/main.py`
  - Adds protected read API for latest quant signals.
- Modify `src/tradecraft/web/static/app.js`
  - Adds a Binance quant panel/table under the Binance tab.
- Modify `src/tradecraft/web/static/style.css`
  - Adds compact dark-table/chip styles for the quant board.
- Modify `src/tradecraft/services/settings_catalog.py`
  - Adds UI-visible quant toggles/limits if config fields are added.
- Modify `src/tradecraft/config.py`
  - Adds env-driven defaults for quant DB path, horizons, and top-N context size.
- Add `tests/test_crypto_quant.py`
  - Unit tests for indicator math, scoring, repository storage, and outcome labeling.
- Modify `tests/test_crypto_market_research.py`
  - Verifies research cycle saves quant signals and latest context includes them.
- Modify `tests/test_binance_block_trader.py`
  - Verifies Binance manager receives quant packets and respects no-trade caution text.
- Modify `tests/test_api_smoke.py`
  - Verifies quant API shape and admin protection.
- Modify `tests/test_config.py`
  - Verifies quant defaults.

---

### Task 1: Add Crypto Quant Config And Repository

**Files:**
- Create: `src/tradecraft/services/crypto_quant.py`
- Modify: `src/tradecraft/config.py`
- Modify: `tests/test_config.py`
- Test: `tests/test_crypto_quant.py`

- [ ] **Step 1: Write failing repository/config tests**

Add this to `tests/test_crypto_quant.py`:

```python
from __future__ import annotations

from pathlib import Path

from tradecraft.services.crypto_quant import (
    CryptoQuantConfig,
    CryptoQuantRepository,
)


def test_quant_repository_saves_latest_signal(tmp_path: Path) -> None:
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))

    repo.save_signal(
        {
            "symbol": "BNBUSDT",
            "horizon": "intraday",
            "long_score": 64.2,
            "short_score": 28.4,
            "no_trade_score": 36.0,
            "expected_r_long": 0.42,
            "expected_r_short": -0.18,
            "signal_json": {
                "symbol": "BNBUSDT",
                "bias": "long",
                "drivers": ["multi-timeframe momentum is positive"],
                "risks": ["spread widened above normal"],
            },
            "updated_at": "2026-05-24T09:00:00+00:00",
        }
    )

    latest = repo.latest_signals(symbols=["BNBUSDT"], limit=5)

    assert len(latest) == 1
    assert latest[0]["symbol"] == "BNBUSDT"
    assert latest[0]["horizon"] == "intraday"
    assert latest[0]["long_score"] == 64.2
    assert latest[0]["short_score"] == 28.4
    assert latest[0]["no_trade_score"] == 36.0
    assert latest[0]["signal"]["bias"] == "long"


def test_quant_config_defaults_are_compact() -> None:
    config = CryptoQuantConfig()

    assert config.db_path == ".runtime/crypto_quant.db"
    assert config.enabled is True
    assert config.context_limit == 16
    assert config.horizons == ("scalp", "intraday", "swing")
```

Add this to `tests/test_config.py`:

```python
def test_crypto_quant_defaults() -> None:
    from tradecraft.config import AppSettings

    settings = AppSettings()

    assert settings.crypto_quant_enabled is True
    assert settings.crypto_quant_db_path == ".runtime/crypto_quant.db"
    assert settings.crypto_quant_context_limit == 16
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_crypto_quant.py::test_quant_repository_saves_latest_signal tests/test_crypto_quant.py::test_quant_config_defaults_are_compact tests/test_config.py::test_crypto_quant_defaults -q
```

Expected: FAIL because `tradecraft.services.crypto_quant` and config fields do not exist.

- [ ] **Step 3: Implement config fields**

In `src/tradecraft/config.py`, add these fields to `AppSettings` near other crypto/binance settings:

```python
    crypto_quant_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("TRADECRAFT_CRYPTO_QUANT_ENABLED", "CRYPTO_QUANT_ENABLED"),
    )
    crypto_quant_db_path: str = Field(
        default=".runtime/crypto_quant.db",
        validation_alias=AliasChoices("TRADECRAFT_CRYPTO_QUANT_DB_PATH", "CRYPTO_QUANT_DB_PATH"),
    )
    crypto_quant_context_limit: int = Field(
        default=16,
        validation_alias=AliasChoices("TRADECRAFT_CRYPTO_QUANT_CONTEXT_LIMIT", "CRYPTO_QUANT_CONTEXT_LIMIT"),
    )
```

- [ ] **Step 4: Implement repository skeleton**

Create `src/tradecraft/services/crypto_quant.py`:

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


@dataclass(slots=True)
class CryptoQuantConfig:
    db_path: str = ".runtime/crypto_quant.db"
    enabled: bool = True
    context_limit: int = 16
    horizons: tuple[str, ...] = ("scalp", "intraday", "swing")


class CryptoQuantRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_quant_signals (
                    symbol TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    long_score REAL NOT NULL DEFAULT 0,
                    short_score REAL NOT NULL DEFAULT 0,
                    no_trade_score REAL NOT NULL DEFAULT 0,
                    expected_r_long REAL NOT NULL DEFAULT 0,
                    expected_r_short REAL NOT NULL DEFAULT 0,
                    signal_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, horizon)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_quant_signals_updated
                ON crypto_quant_signals(updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_quant_signal_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    long_score REAL NOT NULL DEFAULT 0,
                    short_score REAL NOT NULL DEFAULT 0,
                    no_trade_score REAL NOT NULL DEFAULT 0,
                    expected_r_long REAL NOT NULL DEFAULT 0,
                    expected_r_short REAL NOT NULL DEFAULT 0,
                    bias TEXT NOT NULL DEFAULT '',
                    signal_json TEXT NOT NULL DEFAULT '{}',
                    captured_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_quant_signal_history_symbol_time
                ON crypto_quant_signal_history(symbol, horizon, captured_at DESC)
                """
            )

    def save_signal(self, payload: dict[str, Any]) -> None:
        symbol = str(payload.get("symbol") or "").upper().strip()
        horizon = str(payload.get("horizon") or "intraday").strip().lower()
        if not symbol:
            raise ValueError("symbol is required")
        signal_json = json.dumps(payload.get("signal_json") or {}, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_quant_signals (
                    symbol, horizon, long_score, short_score, no_trade_score,
                    expected_r_long, expected_r_short, signal_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, horizon) DO UPDATE SET
                    long_score=excluded.long_score,
                    short_score=excluded.short_score,
                    no_trade_score=excluded.no_trade_score,
                    expected_r_long=excluded.expected_r_long,
                    expected_r_short=excluded.expected_r_short,
                    signal_json=excluded.signal_json,
                    updated_at=excluded.updated_at
                """,
                (
                    symbol,
                    horizon,
                    float(payload.get("long_score") or 0),
                    float(payload.get("short_score") or 0),
                    float(payload.get("no_trade_score") or 0),
                    float(payload.get("expected_r_long") or 0),
                    float(payload.get("expected_r_short") or 0),
                    signal_json,
                    str(payload.get("updated_at") or _utc_now()),
                ),
            )
            signal = payload.get("signal_json") or {}
            conn.execute(
                """
                INSERT INTO crypto_quant_signal_history (
                    symbol, horizon, long_score, short_score, no_trade_score,
                    expected_r_long, expected_r_short, bias, signal_json, captured_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    horizon,
                    float(payload.get("long_score") or 0),
                    float(payload.get("short_score") or 0),
                    float(payload.get("no_trade_score") or 0),
                    float(payload.get("expected_r_long") or 0),
                    float(payload.get("expected_r_short") or 0),
                    str(signal.get("bias") or ""),
                    signal_json,
                    str(payload.get("updated_at") or _utc_now()),
                ),
            )

    def latest_signals(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        clean_symbols = [str(symbol).upper().strip() for symbol in symbols or [] if str(symbol).strip()]
        with self._connect() as conn:
            if clean_symbols:
                placeholders = ",".join("?" for _ in clean_symbols)
                rows = conn.execute(
                    f"""
                    SELECT * FROM crypto_quant_signals
                    WHERE symbol IN ({placeholders})
                    ORDER BY updated_at DESC, long_score + short_score DESC
                    LIMIT ?
                    """,
                    (*clean_symbols, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM crypto_quant_signals
                    ORDER BY updated_at DESC, long_score + short_score DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
        return [
            {
                "symbol": str(row["symbol"]),
                "horizon": str(row["horizon"]),
                "long_score": float(row["long_score"]),
                "short_score": float(row["short_score"]),
                "no_trade_score": float(row["no_trade_score"]),
                "expected_r_long": float(row["expected_r_long"]),
                "expected_r_short": float(row["expected_r_short"]),
                "signal": _json_loads(str(row["signal_json"]), {}),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def signal_history(
        self,
        *,
        symbol: str,
        horizon: str = "intraday",
        limit: int = 48,
    ) -> list[dict[str, Any]]:
        clean_symbol = str(symbol).upper().strip()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM crypto_quant_signal_history
                WHERE symbol = ? AND horizon = ?
                ORDER BY captured_at DESC
                LIMIT ?
                """,
                (clean_symbol, str(horizon), int(limit)),
            ).fetchall()
        return [
            {
                "symbol": str(row["symbol"]),
                "horizon": str(row["horizon"]),
                "long_score": float(row["long_score"]),
                "short_score": float(row["short_score"]),
                "no_trade_score": float(row["no_trade_score"]),
                "expected_r_long": float(row["expected_r_long"]),
                "expected_r_short": float(row["expected_r_short"]),
                "bias": str(row["bias"]),
                "signal": _json_loads(str(row["signal_json"]), {}),
                "captured_at": str(row["captured_at"]),
            }
            for row in rows
        ]

    def retrieval_context(
        self,
        *,
        symbols: list[str],
        horizon: str = "intraday",
        points_per_symbol: int = 12,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for symbol in symbols:
            history = self.signal_history(
                symbol=symbol,
                horizon=horizon,
                limit=points_per_symbol,
            )
            if not history:
                continue
            latest = history[0]
            long_values = [float(row["long_score"]) for row in history]
            short_values = [float(row["short_score"]) for row in history]
            no_trade_values = [float(row["no_trade_score"]) for row in history]
            items.append(
                {
                    "symbol": str(symbol).upper(),
                    "horizon": horizon,
                    "latest": latest,
                    "history_points": len(history),
                    "trend": {
                        "long_score_delta": round(long_values[0] - long_values[-1], 3),
                        "short_score_delta": round(short_values[0] - short_values[-1], 3),
                        "no_trade_score_delta": round(no_trade_values[0] - no_trade_values[-1], 3),
                    },
                    "recent_biases": [str(row["bias"]) for row in history[:5]],
                }
            )
        return {
            "status": "ok",
            "horizon": horizon,
            "items": items,
        }
```

- [ ] **Step 5: Run repository/config tests**

Run:

```bash
pytest tests/test_crypto_quant.py tests/test_config.py::test_crypto_quant_defaults -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/services/crypto_quant.py src/tradecraft/config.py tests/test_crypto_quant.py tests/test_config.py
git commit -m "feat: add crypto quant signal repository"
```

---

### Task 2: Implement Indicator Math And Directional Scoring

**Files:**
- Modify: `src/tradecraft/services/crypto_quant.py`
- Test: `tests/test_crypto_quant.py`

- [ ] **Step 1: Write failing indicator tests**

Add this to `tests/test_crypto_quant.py`:

```python
from tradecraft.services.crypto_quant import CryptoQuantEngine


def _bars(closes: list[float]) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for index, close in enumerate(closes):
        rows.append(
            {
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000.0 + index * 100.0,
                "quote_volume": (1000.0 + index * 100.0) * close,
            }
        )
    return rows


def test_quant_engine_scores_long_when_trend_and_volume_confirm() -> None:
    engine = CryptoQuantEngine()
    signal = engine.build_signal(
        symbol="TESTUSDT",
        horizon="intraday",
        klines_by_interval={
            "5m": _bars([100, 101, 102, 103, 104, 105, 106, 107, 109, 111]),
            "15m": _bars([100, 101, 102, 103, 104, 106, 108, 110, 112, 114]),
            "1h": _bars([100, 102, 103, 104, 106, 108, 110, 112, 115, 118]),
        },
        market_features={
            "spread_bps": 1.2,
            "funding_rate": 0.00005,
            "mark_index_basis_pct": 0.03,
            "open_interest": 2_000_000,
            "timeframe_alignment": "bullish",
        },
        btc_closes=[100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
        eth_closes=[100, 100.5, 101, 102, 103, 104, 105, 106, 107, 108],
    )

    assert signal["bias"] == "long"
    assert signal["long_score"] > signal["short_score"]
    assert signal["long_score"] >= 60
    assert signal["no_trade_score"] < 50
    assert signal["metrics"]["atr_pct"] > 0
    assert signal["metrics"]["rsi"] >= 50


def test_quant_engine_raises_no_trade_when_spread_and_extension_are_high() -> None:
    engine = CryptoQuantEngine()
    signal = engine.build_signal(
        symbol="WIDEUSDT",
        horizon="intraday",
        klines_by_interval={
            "5m": _bars([100, 104, 109, 116, 124, 133, 143, 154, 166, 179]),
            "15m": _bars([100, 105, 111, 118, 126, 135, 145, 156, 168, 181]),
        },
        market_features={
            "spread_bps": 18.0,
            "funding_rate": 0.0009,
            "mark_index_basis_pct": 0.20,
            "open_interest": 10_000_000,
            "timeframe_alignment": "bullish",
        },
        btc_closes=[100, 101, 102, 102, 103, 103, 104, 104, 105, 105],
        eth_closes=[100, 101, 101, 102, 102, 103, 103, 104, 104, 105],
    )

    assert signal["bias"] == "no_trade"
    assert signal["no_trade_score"] >= 60
    assert "spread is expensive" in signal["risks"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_crypto_quant.py::test_quant_engine_scores_long_when_trend_and_volume_confirm tests/test_crypto_quant.py::test_quant_engine_raises_no_trade_when_spread_and_extension_are_high -q
```

Expected: FAIL because `CryptoQuantEngine` does not exist.

- [ ] **Step 3: Implement quant engine helpers**

Append this to `src/tradecraft/services/crypto_quant.py`:

```python
def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(min(value, high), low)


def _series_from_bars(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [_to_float(row.get(key)) for row in rows if _to_float(row.get(key)) > 0]


def _pct_change(values: list[float]) -> float:
    if len(values) < 2 or values[0] <= 0:
        return 0.0
    return (values[-1] - values[0]) / values[0] * 100.0


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) < 2:
        return 50.0
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    recent = deltas[-period:]
    gains = [max(delta, 0.0) for delta in recent]
    losses = [abs(min(delta, 0.0)) for delta in recent]
    avg_gain = sum(gains) / max(len(gains), 1)
    avg_loss = sum(losses) / max(len(losses), 1)
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr_pct(rows: list[dict[str, Any]], period: int = 14) -> float:
    if len(rows) < 2:
        return 0.0
    true_ranges: list[float] = []
    previous_close = _to_float(rows[0].get("close"))
    for row in rows[1:]:
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        close = _to_float(row.get("close"))
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = close
    recent = true_ranges[-period:]
    last_close = _to_float(rows[-1].get("close"))
    if not recent or last_close <= 0:
        return 0.0
    return (sum(recent) / len(recent)) / last_close * 100.0


def _volume_zscore(rows: list[dict[str, Any]], lookback: int = 20) -> float:
    volumes = _series_from_bars(rows, "volume")[-lookback:]
    if len(volumes) < 3:
        return 0.0
    mean = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
    variance = sum((value - mean) ** 2 for value in volumes[:-1]) / max(len(volumes) - 1, 1)
    stdev = variance ** 0.5
    if stdev <= 0:
        return 0.0
    return (volumes[-1] - mean) / stdev


def _correlation(left: list[float], right: list[float]) -> float:
    length = min(len(left), len(right))
    if length < 3:
        return 0.0
    left = left[-length:]
    right = right[-length:]
    mean_left = sum(left) / length
    mean_right = sum(right) / length
    numerator = sum((left[i] - mean_left) * (right[i] - mean_right) for i in range(length))
    denom_left = sum((value - mean_left) ** 2 for value in left) ** 0.5
    denom_right = sum((value - mean_right) ** 2 for value in right) ** 0.5
    if denom_left <= 0 or denom_right <= 0:
        return 0.0
    return numerator / (denom_left * denom_right)


class CryptoQuantEngine:
    def build_signal(
        self,
        *,
        symbol: str,
        horizon: str,
        klines_by_interval: dict[str, list[dict[str, Any]]],
        market_features: dict[str, Any],
        btc_closes: list[float] | None = None,
        eth_closes: list[float] | None = None,
    ) -> dict[str, Any]:
        primary_rows = (
            klines_by_interval.get("15m")
            or klines_by_interval.get("5m")
            or klines_by_interval.get("1h")
            or []
        )
        closes = _series_from_bars(primary_rows, "close")
        momentum_by_interval = {
            interval: _pct_change(_series_from_bars(rows, "close"))
            for interval, rows in klines_by_interval.items()
        }
        avg_momentum = sum(momentum_by_interval.values()) / max(len(momentum_by_interval), 1)
        ema_fast = _ema(closes[-21:], 8)
        ema_slow = _ema(closes[-34:], 21)
        ema_slope_pct = ((ema_fast - ema_slow) / ema_slow * 100.0) if ema_slow > 0 else 0.0
        rsi = _rsi(closes)
        atr_pct = _atr_pct(primary_rows)
        volume_z = _volume_zscore(primary_rows)
        spread_bps = _to_float(market_features.get("spread_bps"))
        funding = _to_float(market_features.get("funding_rate"))
        basis = _to_float(market_features.get("mark_index_basis_pct"))
        btc_corr = _correlation(closes, btc_closes or [])
        eth_corr = _correlation(closes, eth_closes or [])

        long_score = 50.0
        short_score = 50.0
        no_trade_score = 20.0
        drivers: list[str] = []
        risks: list[str] = []

        if avg_momentum > 0.35:
            long_score += min(avg_momentum * 4.0, 25.0)
            short_score -= min(avg_momentum * 2.5, 20.0)
            drivers.append("multi-timeframe momentum is positive")
        elif avg_momentum < -0.35:
            short_score += min(abs(avg_momentum) * 4.0, 25.0)
            long_score -= min(abs(avg_momentum) * 2.5, 20.0)
            drivers.append("multi-timeframe momentum is negative")

        if ema_slope_pct > 0.1:
            long_score += 10.0
            drivers.append("fast EMA is above slow EMA")
        elif ema_slope_pct < -0.1:
            short_score += 10.0
            drivers.append("fast EMA is below slow EMA")

        if volume_z >= 1.0:
            long_score += 5.0 if avg_momentum >= 0 else 0.0
            short_score += 5.0 if avg_momentum < 0 else 0.0
            drivers.append("volume expansion confirms direction")

        if rsi >= 78:
            no_trade_score += 20.0
            short_score += 4.0
            risks.append("upside is extended by RSI")
        elif rsi <= 22:
            no_trade_score += 20.0
            long_score += 4.0
            risks.append("downside is extended by RSI")

        if spread_bps >= 8.0:
            no_trade_score += min(spread_bps, 30.0)
            risks.append("spread is expensive")

        if abs(funding) >= 0.0005:
            no_trade_score += 10.0
            risks.append("funding is crowded")
        if abs(basis) >= 0.15:
            no_trade_score += 10.0
            risks.append("mark/index basis is stretched")
        if atr_pct >= 8.0:
            no_trade_score += 10.0
            risks.append("intraday volatility is elevated")

        long_score = _clamp(long_score)
        short_score = _clamp(short_score)
        no_trade_score = _clamp(no_trade_score)
        if no_trade_score >= 60 or max(long_score, short_score) < 55:
            bias = "no_trade"
        elif long_score >= short_score:
            bias = "long"
        else:
            bias = "short"

        expected_r_long = (long_score - no_trade_score) / 100.0
        expected_r_short = (short_score - no_trade_score) / 100.0
        return {
            "symbol": str(symbol).upper(),
            "horizon": horizon,
            "bias": bias,
            "long_score": round(long_score, 2),
            "short_score": round(short_score, 2),
            "no_trade_score": round(no_trade_score, 2),
            "expected_r_long": round(expected_r_long, 3),
            "expected_r_short": round(expected_r_short, 3),
            "drivers": drivers,
            "risks": risks,
            "metrics": {
                "avg_momentum_pct": round(avg_momentum, 4),
                "ema_slope_pct": round(ema_slope_pct, 4),
                "rsi": round(rsi, 2),
                "atr_pct": round(atr_pct, 4),
                "volume_z": round(volume_z, 4),
                "spread_bps": round(spread_bps, 4),
                "funding_rate": round(funding, 8),
                "mark_index_basis_pct": round(basis, 4),
                "btc_corr": round(btc_corr, 4),
                "eth_corr": round(eth_corr, 4),
            },
        }
```

- [ ] **Step 4: Run indicator tests**

Run:

```bash
pytest tests/test_crypto_quant.py::test_quant_engine_scores_long_when_trend_and_volume_confirm tests/test_crypto_quant.py::test_quant_engine_raises_no_trade_when_spread_and_extension_are_high -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/services/crypto_quant.py tests/test_crypto_quant.py
git commit -m "feat: score crypto long short quant signals"
```

---

### Task 3: Generate Quant Signals From Crypto Research Data

**Files:**
- Modify: `src/tradecraft/services/crypto_quant.py`
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_crypto_market_research.py`

- [ ] **Step 1: Write failing integration test**

Add this to `tests/test_crypto_market_research.py`:

```python
def test_research_cycle_persists_quant_signals(tmp_path: Path) -> None:
    from tradecraft.services.crypto_quant import CryptoQuantConfig, CryptoQuantRepository

    quant_repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        binance=MultiFrameBinance(),
    )
    service.quant_repository = quant_repo

    result = asyncio.run(service.collect_once(symbols=["BTCUSDT"], max_symbols=1))
    signals = quant_repo.latest_signals(symbols=["BTCUSDT"], limit=5)

    assert result["status"] == "ok"
    assert signals
    assert signals[0]["symbol"] == "BTCUSDT"
    assert signals[0]["horizon"] == "intraday"
    assert "metrics" in signals[0]["signal"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_crypto_market_research.py::test_research_cycle_persists_quant_signals -q
```

Expected: FAIL because `CryptoMarketResearchService` does not call a quant repository.

- [ ] **Step 3: Add optional quant repository wiring**

In `src/tradecraft/services/crypto_market_research.py`, import:

```python
from tradecraft.services.crypto_quant import CryptoQuantEngine, CryptoQuantRepository
```

In `CryptoMarketResearchService.__init__`, add:

```python
        self.quant_repository: CryptoQuantRepository | None = None
        self.quant_engine = CryptoQuantEngine()
```

After each symbol snapshot is normalized and saved in `collect_once`, call a new helper:

```python
            self._save_quant_signals_from_snapshot(symbol=symbol, snapshot=snapshot, features=features)
```

Add this method to `CryptoMarketResearchService`:

```python
    def _save_quant_signals_from_snapshot(
        self,
        *,
        symbol: str,
        snapshot: dict[str, Any],
        features: dict[str, Any],
    ) -> None:
        if self.quant_repository is None:
            return
        klines_by_interval = _dict_or_empty(snapshot.get("klines_by_interval"))
        if not klines_by_interval:
            return
        for horizon in ("scalp", "intraday", "swing"):
            signal = self.quant_engine.build_signal(
                symbol=symbol,
                horizon=horizon,
                klines_by_interval={
                    str(interval): rows
                    for interval, rows in klines_by_interval.items()
                    if isinstance(rows, list)
                },
                market_features=features,
            )
            self.quant_repository.save_signal(
                {
                    "symbol": symbol,
                    "horizon": horizon,
                    "long_score": signal["long_score"],
                    "short_score": signal["short_score"],
                    "no_trade_score": signal["no_trade_score"],
                    "expected_r_long": signal["expected_r_long"],
                    "expected_r_short": signal["expected_r_short"],
                    "signal_json": signal,
                    "updated_at": _utc_now(),
                }
            )
```

- [ ] **Step 4: Wire runtime construction**

In `src/tradecraft/runtime/binance_block_trader_runner.py`, when constructing `CryptoMarketResearchService`, create and assign a quant repository:

```python
from tradecraft.services.crypto_quant import CryptoQuantRepository

crypto_research = CryptoMarketResearchService(...)
crypto_research.quant_repository = CryptoQuantRepository(settings.crypto_quant_db_path)
```

If the exact local variable is not named `crypto_research`, assign the repository immediately after the existing service construction.

- [ ] **Step 5: Run integration test**

Run:

```bash
pytest tests/test_crypto_market_research.py::test_research_cycle_persists_quant_signals -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/services/crypto_market_research.py src/tradecraft/runtime/binance_block_trader_runner.py tests/test_crypto_market_research.py
git commit -m "feat: persist quant signals from crypto research"
```

---

### Task 3B: Add Quant Time-Series Retrieval For Jue

**Files:**
- Modify: `src/tradecraft/services/crypto_quant.py`
- Test: `tests/test_crypto_quant.py`

- [ ] **Step 1: Write failing history/retrieval test**

Add this to `tests/test_crypto_quant.py`:

```python
def test_quant_repository_keeps_signal_history_for_jue_retrieval(tmp_path: Path) -> None:
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    for index, long_score in enumerate([42.0, 55.0, 68.0]):
        repo.save_signal(
            {
                "symbol": "BNBUSDT",
                "horizon": "intraday",
                "long_score": long_score,
                "short_score": 70.0 - long_score,
                "no_trade_score": 35.0,
                "expected_r_long": (long_score - 35.0) / 100.0,
                "expected_r_short": ((70.0 - long_score) - 35.0) / 100.0,
                "signal_json": {
                    "bias": "long" if long_score >= 55 else "short",
                    "metrics": {"atr_pct": 1.4 + index, "rsi": 45 + index},
                },
                "updated_at": f"2026-05-24T09:0{index}:00+00:00",
            }
        )

    latest = repo.latest_signals(symbols=["BNBUSDT"], limit=1)
    history = repo.signal_history(symbol="BNBUSDT", horizon="intraday", limit=10)
    context = repo.retrieval_context(symbols=["BNBUSDT"], horizon="intraday", points_per_symbol=3)

    assert len(latest) == 1
    assert latest[0]["long_score"] == 68.0
    assert len(history) == 3
    assert history[0]["captured_at"] == "2026-05-24T09:02:00+00:00"
    assert context["items"][0]["history_points"] == 3
    assert context["items"][0]["trend"]["long_score_delta"] == 26.0
    assert context["items"][0]["recent_biases"] == ["long", "long", "short"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_crypto_quant.py::test_quant_repository_keeps_signal_history_for_jue_retrieval -q
```

Expected: FAIL until `crypto_quant_signal_history`, `signal_history`, and `retrieval_context` are implemented.

- [ ] **Step 3: Implement time-series history**

Use the `crypto_quant_signal_history` schema and methods already specified in Task 1:

```python
CREATE TABLE IF NOT EXISTS crypto_quant_signal_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    horizon TEXT NOT NULL,
    long_score REAL NOT NULL DEFAULT 0,
    short_score REAL NOT NULL DEFAULT 0,
    no_trade_score REAL NOT NULL DEFAULT 0,
    expected_r_long REAL NOT NULL DEFAULT 0,
    expected_r_short REAL NOT NULL DEFAULT 0,
    bias TEXT NOT NULL DEFAULT '',
    signal_json TEXT NOT NULL DEFAULT '{}',
    captured_at TEXT NOT NULL
)
```

Every `save_signal()` call must update `crypto_quant_signals` for fast latest lookup and append one row to `crypto_quant_signal_history` for 시계열 analysis.

- [ ] **Step 4: Implement compact retrieval context**

Add `CryptoQuantRepository.retrieval_context(symbols, horizon, points_per_symbol)` exactly as described in Task 1. It must return:

```python
{
    "status": "ok",
    "horizon": "intraday",
    "items": [
        {
            "symbol": "BNBUSDT",
            "latest": {"long_score": 68.0, "short_score": 2.0},
            "history_points": 3,
            "trend": {
                "long_score_delta": 26.0,
                "short_score_delta": -26.0,
                "no_trade_score_delta": 0.0,
            },
            "recent_biases": ["long", "long", "short"],
        }
    ],
}
```

- [ ] **Step 5: Run retrieval test**

Run:

```bash
pytest tests/test_crypto_quant.py::test_quant_repository_keeps_signal_history_for_jue_retrieval -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/services/crypto_quant.py tests/test_crypto_quant.py
git commit -m "feat: keep crypto quant signal history"
```

---

### Task 4: Inject Quant Packets Into Binance Jue Manager

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing manager prompt test**

Add this to `tests/test_binance_block_trader.py`:

```python
def test_manager_prompt_includes_crypto_quant_context(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class QuantProvider:
        def latest_signals(self, *, symbols: list[str] | None = None, limit: int = 16) -> list[dict[str, object]]:
            return [
                {
                    "symbol": "BNBUSDT",
                    "horizon": "intraday",
                    "long_score": 22.0,
                    "short_score": 68.0,
                    "no_trade_score": 48.0,
                    "expected_r_long": -0.26,
                    "expected_r_short": 0.20,
                    "signal": {
                        "bias": "short",
                        "drivers": ["fast EMA is below slow EMA"],
                        "risks": ["funding is crowded"],
                        "metrics": {"atr_pct": 1.8, "rsi": 44.0},
                    },
                    "updated_at": "2026-05-24T09:00:00+00:00",
                }
            ]

    class LLM:
        def ask_json(self, payload: dict[str, object]) -> dict[str, object]:
            captured["prompt"] = payload
            return {"create_blocks": [], "update_blocks": [], "close_blocks": [], "pause_blocks": []}

    trader = _make_trader(tmp_path, codex_runtime=LLM())
    trader.quant_provider = QuantProvider()

    asyncio.run(trader.run_manager_once(candidates=[{"symbol": "BNBUSDT"}]))

    prompt = captured["prompt"]
    assert "crypto_quant" in prompt
    assert "crypto_quant" in prompt["decision_inputs"]
    assert prompt["crypto_quant"]["items"][0]["symbol"] == "BNBUSDT"
    assert prompt["crypto_quant"]["items"][0]["signal"]["bias"] == "short"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_manager_prompt_includes_crypto_quant_context -q
```

Expected: FAIL because `quant_provider` is not supported.

- [ ] **Step 3: Add quant provider to trader**

In `BinanceBlockTrader.__init__`, add an optional constructor parameter:

```python
        quant_provider: Any | None = None,
```

Assign:

```python
        self.quant_provider = quant_provider
```

Add helper:

```python
    def _crypto_quant_context(self, *, symbols: list[str]) -> dict[str, Any]:
        if self.quant_provider is None:
            return {"status": "missing", "items": []}
        try:
            latest_signals = getattr(self.quant_provider, "latest_signals")
            items = latest_signals(symbols=symbols, limit=max(len(symbols), 16))
            retrieval_context = getattr(self.quant_provider, "retrieval_context", None)
            history = (
                retrieval_context(
                    symbols=symbols,
                    horizon="intraday",
                    points_per_symbol=12,
                )
                if retrieval_context is not None
                else {"status": "missing", "items": []}
            )
        except Exception as exc:
            logger.warning("binance crypto quant context failed: %s", exc)
            return {"status": "error", "error": str(exc), "items": []}
        return {
            "status": "ok",
            "items": items,
            "history": history,
            "policy": {
                "meaning": "Quant scores are directional evidence. They adjust conviction and sizing, not safety gates.",
                "biases": ["long", "short", "no_trade"],
                "preferred_use": "Compare latest scores and short history trends before creating blocks.",
            },
        }
```

In `run_manager_once`, after `crypto_alpha` is built, add:

```python
        crypto_quant = self._crypto_quant_context(symbols=symbols)
```

Add to prompt:

```python
            "crypto_quant": crypto_quant,
```

Add to `decision_inputs`:

```python
                "crypto_quant",
```

Add policy text near `crypto_alpha_policy`:

```python
                "crypto_quant_policy": (
                    "Use crypto_quant as the compact directional packet. Prefer creating "
                    "blocks when the selected side has a clear score advantage and "
                    "no_trade_score is not dominant. If no_trade_score is high, reduce "
                    "size or wait for a better entry rather than forcing activity."
                ),
```

- [ ] **Step 4: Wire runner provider**

In `src/tradecraft/runtime/binance_block_trader_runner.py`, construct:

```python
from tradecraft.services.crypto_quant import CryptoQuantRepository

quant_repository = CryptoQuantRepository(settings.crypto_quant_db_path)
```

Pass it into `BinanceBlockTrader`:

```python
quant_provider=quant_repository,
```

- [ ] **Step 5: Run manager prompt test**

Run:

```bash
pytest tests/test_binance_block_trader.py::test_manager_prompt_includes_crypto_quant_context -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/services/binance_block_trader.py src/tradecraft/runtime/binance_block_trader_runner.py tests/test_binance_block_trader.py
git commit -m "feat: inject crypto quant into binance manager"
```

---

### Task 5: Add Quant Outcome Labeling

**Files:**
- Modify: `src/tradecraft/services/crypto_quant.py`
- Test: `tests/test_crypto_quant.py`

- [ ] **Step 1: Write failing outcome test**

Add this to `tests/test_crypto_quant.py`:

```python
def test_outcome_labeler_detects_target_before_stop_for_long() -> None:
    from tradecraft.services.crypto_quant import CryptoQuantOutcomeLabeler

    labeler = CryptoQuantOutcomeLabeler()
    label = labeler.label_path(
        symbol="BTCUSDT",
        side="long",
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        rows=[
            {"high": 103.0, "low": 99.0, "close": 102.0},
            {"high": 111.0, "low": 101.0, "close": 110.5},
            {"high": 112.0, "low": 94.0, "close": 96.0},
        ],
        horizon="1h",
    )

    assert label["outcome"] == "target_first"
    assert label["r_multiple"] == 2.0
    assert label["mfe_r"] >= 2.0
    assert label["mae_r"] > -1.0


def test_outcome_labeler_detects_stop_before_target_for_short() -> None:
    from tradecraft.services.crypto_quant import CryptoQuantOutcomeLabeler

    labeler = CryptoQuantOutcomeLabeler()
    label = labeler.label_path(
        symbol="BNBUSDT",
        side="short",
        entry_price=100.0,
        stop_price=105.0,
        target_price=90.0,
        rows=[
            {"high": 106.0, "low": 98.0, "close": 105.5},
            {"high": 107.0, "low": 89.0, "close": 91.0},
        ],
        horizon="1h",
    )

    assert label["outcome"] == "stop_first"
    assert label["r_multiple"] == -1.0
    assert label["mae_r"] <= -1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_crypto_quant.py::test_outcome_labeler_detects_target_before_stop_for_long tests/test_crypto_quant.py::test_outcome_labeler_detects_stop_before_target_for_short -q
```

Expected: FAIL because `CryptoQuantOutcomeLabeler` does not exist.

- [ ] **Step 3: Implement outcome labeler**

Append this to `src/tradecraft/services/crypto_quant.py`:

```python
class CryptoQuantOutcomeLabeler:
    def label_path(
        self,
        *,
        symbol: str,
        side: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        rows: list[dict[str, Any]],
        horizon: str,
    ) -> dict[str, Any]:
        entry = _to_float(entry_price)
        stop = _to_float(stop_price)
        target = _to_float(target_price)
        normalized_side = str(side or "long").lower()
        risk = abs(entry - stop)
        if entry <= 0 or stop <= 0 or target <= 0 or risk <= 0:
            return {"symbol": symbol, "horizon": horizon, "outcome": "invalid", "r_multiple": 0.0}

        outcome = "open_at_horizon"
        exit_price = _to_float(rows[-1].get("close")) if rows else entry
        mfe_r = 0.0
        mae_r = 0.0

        for row in rows:
            high = _to_float(row.get("high"))
            low = _to_float(row.get("low"))
            if normalized_side == "short":
                favorable_r = (entry - low) / risk
                adverse_r = (entry - high) / risk
                mfe_r = max(mfe_r, favorable_r)
                mae_r = min(mae_r, adverse_r)
                if high >= stop:
                    outcome = "stop_first"
                    exit_price = stop
                    break
                if low <= target:
                    outcome = "target_first"
                    exit_price = target
                    break
            else:
                favorable_r = (high - entry) / risk
                adverse_r = (low - entry) / risk
                mfe_r = max(mfe_r, favorable_r)
                mae_r = min(mae_r, adverse_r)
                if low <= stop:
                    outcome = "stop_first"
                    exit_price = stop
                    break
                if high >= target:
                    outcome = "target_first"
                    exit_price = target
                    break

        if outcome == "target_first":
            r_multiple = abs(target - entry) / risk
        elif outcome == "stop_first":
            r_multiple = -1.0
        elif normalized_side == "short":
            r_multiple = (entry - exit_price) / risk
        else:
            r_multiple = (exit_price - entry) / risk

        return {
            "symbol": str(symbol).upper(),
            "side": normalized_side,
            "horizon": horizon,
            "outcome": outcome,
            "exit_price": exit_price,
            "r_multiple": round(r_multiple, 4),
            "mfe_r": round(mfe_r, 4),
            "mae_r": round(mae_r, 4),
        }
```

- [ ] **Step 4: Run outcome tests**

Run:

```bash
pytest tests/test_crypto_quant.py::test_outcome_labeler_detects_target_before_stop_for_long tests/test_crypto_quant.py::test_outcome_labeler_detects_stop_before_target_for_short -q
```

Expected: PASS.

- [ ] **Step 5: Add storage for quant outcomes**

In `CryptoQuantRepository._ensure_schema`, add:

```python
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_quant_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    source_id TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT '',
                    r_multiple REAL NOT NULL DEFAULT 0,
                    mfe_r REAL NOT NULL DEFAULT 0,
                    mae_r REAL NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    labeled_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_quant_outcomes_symbol_time
                ON crypto_quant_outcomes(symbol, labeled_at DESC)
                """
            )
```

Add method:

```python
    def save_outcome(self, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO crypto_quant_outcomes (
                    symbol, side, horizon, source_id, outcome,
                    r_multiple, mfe_r, mae_r, payload_json, labeled_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("symbol") or "").upper(),
                    str(payload.get("side") or ""),
                    str(payload.get("horizon") or ""),
                    str(payload.get("source_id") or ""),
                    str(payload.get("outcome") or ""),
                    float(payload.get("r_multiple") or 0),
                    float(payload.get("mfe_r") or 0),
                    float(payload.get("mae_r") or 0),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    str(payload.get("labeled_at") or _utc_now()),
                ),
            )
```

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/services/crypto_quant.py tests/test_crypto_quant.py
git commit -m "feat: label crypto quant outcomes"
```

---

### Task 6: Expose Quant API

**Files:**
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_api_smoke.py`

- [ ] **Step 1: Write failing API test**

Add this to `tests/test_api_smoke.py`:

```python
def test_crypto_quant_signals_api_requires_admin(client, monkeypatch, tmp_path) -> None:
    from tradecraft import main
    from tradecraft.services.crypto_quant import CryptoQuantRepository

    monkeypatch.setattr(main.settings, "admin_token", "secret")
    monkeypatch.setattr(main.settings, "crypto_quant_db_path", str(tmp_path / "quant.db"))
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    repo.save_signal(
        {
            "symbol": "BTCUSDT",
            "horizon": "intraday",
            "long_score": 70,
            "short_score": 20,
            "no_trade_score": 25,
            "expected_r_long": 0.45,
            "expected_r_short": -0.1,
            "signal_json": {"bias": "long"},
            "updated_at": "2026-05-24T09:00:00+00:00",
        }
    )

    blocked = client.get("/api/binance/quant/signals")
    assert blocked.status_code == 401

    ok = client.get(
        "/api/binance/quant/signals",
        headers={"Authorization": "Bearer secret"},
    )
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["status"] == "ok"
    assert payload["items"][0]["symbol"] == "BTCUSDT"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_api_smoke.py::test_crypto_quant_signals_api_requires_admin -q
```

Expected: FAIL because the route does not exist.

- [ ] **Step 3: Add route**

In `src/tradecraft/main.py`, import:

```python
from tradecraft.services.crypto_quant import CryptoQuantRepository
```

Add a protected route near other Binance routes:

```python
@app.get("/api/binance/quant/signals")
def api_binance_quant_signals(
    request: Request,
    symbols: str = "",
    limit: int = 16,
) -> dict[str, Any]:
    require_admin(request)
    clean_symbols = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    repo = CryptoQuantRepository(settings.crypto_quant_db_path)
    items = repo.latest_signals(symbols=clean_symbols or None, limit=max(min(limit, 100), 1))
    return {
        "status": "ok",
        "items": items,
        "count": len(items),
    }
```

Use the existing admin helper name from `main.py`; if it is named differently, keep the same auth behavior used by `/api/kis/**` and `/api/memory/**`.

- [ ] **Step 4: Run API test**

Run:

```bash
pytest tests/test_api_smoke.py::test_crypto_quant_signals_api_requires_admin -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/main.py tests/test_api_smoke.py
git commit -m "feat: expose binance quant signals api"
```

---

### Task 7: Add Binance Quant Board To UI

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`

- [ ] **Step 1: Add UI state**

In `src/tradecraft/web/static/app.js`, extend `state.binanceTrader`:

```javascript
  quantSignals: [],
  quantError: "",
```

- [ ] **Step 2: Fetch quant signals with Binance status**

In `loadBinanceBlocks`, after status fetch succeeds, add:

```javascript
    try {
      const quantPayload = await getJSON("/binance/quant/signals?limit=24");
      state.binanceTrader.quantSignals = Array.isArray(quantPayload.items) ? quantPayload.items : [];
      state.binanceTrader.quantError = "";
    } catch (error) {
      state.binanceTrader.quantSignals = [];
      state.binanceTrader.quantError = getErrorMessage(error);
    }
```

- [ ] **Step 3: Render quant board**

Add helper near `renderBinanceTrader`:

```javascript
function renderBinanceQuantBoard() {
  const rows = state.binanceTrader.quantSignals || [];
  if (state.binanceTrader.quantError) {
    return `<div class="notice">퀀트 신호 조회 실패: ${escapeHTML(state.binanceTrader.quantError)}</div>`;
  }
  if (!rows.length) {
    return `<div class="notice">아직 저장된 바이낸스 퀀트 신호가 없습니다.</div>`;
  }
  const body = rows.map((item) => {
    const signal = item.signal || {};
    const metrics = signal.metrics || {};
    const bias = signal.bias || "unknown";
    return `
      <tr>
        <td><strong>${escapeHTML(item.symbol || "-")}</strong><span>${escapeHTML(item.horizon || "-")}</span></td>
        <td><span class="quant-bias ${escapeHTML(bias)}">${escapeHTML(bias)}</span></td>
        <td class="num">${formatNumber(item.long_score, 1)}</td>
        <td class="num">${formatNumber(item.short_score, 1)}</td>
        <td class="num">${formatNumber(item.no_trade_score, 1)}</td>
        <td class="num">${formatNumber(metrics.atr_pct, 2)}%</td>
        <td class="num">${formatNumber(metrics.rsi, 1)}</td>
        <td class="num">${formatNumber(metrics.spread_bps, 2)}</td>
      </tr>
    `;
  }).join("");
  return `
    <section class="memory-section binance-quant-panel">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Quant Packet</p>
          <h3>롱/숏/관망 정량 신호</h3>
        </div>
      </div>
      <div class="quant-table-wrap">
        <table class="quant-table">
          <thead>
            <tr>
              <th>심볼</th>
              <th>Bias</th>
              <th>Long</th>
              <th>Short</th>
              <th>No Trade</th>
              <th>ATR</th>
              <th>RSI</th>
              <th>Spread</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </section>
  `;
}
```

Insert `${renderBinanceQuantBoard()}` inside `renderBinanceTrader()` below the existing edge/performance panel and above block cards.

- [ ] **Step 4: Add CSS**

In `src/tradecraft/web/static/style.css`, add:

```css
.binance-quant-panel {
  overflow: hidden;
}

.quant-table-wrap {
  overflow-x: auto;
}

.quant-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.86rem;
}

.quant-table th,
.quant-table td {
  border-bottom: 1px solid var(--border-subtle);
  padding: 0.65rem 0.7rem;
  text-align: left;
  white-space: nowrap;
}

.quant-table th {
  color: var(--text-muted);
  font-weight: 600;
}

.quant-table td span {
  display: block;
  color: var(--text-muted);
  font-size: 0.74rem;
  margin-top: 0.15rem;
}

.quant-table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.quant-bias {
  display: inline-flex;
  border: 1px solid var(--border-subtle);
  border-radius: 999px;
  padding: 0.18rem 0.48rem;
}

.quant-bias.long {
  color: var(--status-gain);
}

.quant-bias.short {
  color: var(--status-loss);
}

.quant-bias.no_trade {
  color: var(--status-warning);
}
```

- [ ] **Step 5: Run JS syntax check**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css
git commit -m "feat: show binance quant board"
```

---

### Task 8: Settings Catalog And Operational Defaults

**Files:**
- Modify: `src/tradecraft/services/settings_catalog.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing settings metadata test**

Add to `tests/test_config.py`:

```python
def test_crypto_quant_settings_are_visible() -> None:
    from tradecraft.services.settings_catalog import SETTINGS_CATALOG

    assert "crypto_quant_enabled" in SETTINGS_CATALOG
    assert SETTINGS_CATALOG["crypto_quant_enabled"].group == "binance"
    assert "crypto_quant_context_limit" in SETTINGS_CATALOG
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_config.py::test_crypto_quant_settings_are_visible -q
```

Expected: FAIL because metadata does not exist.

- [ ] **Step 3: Add catalog entries**

In `src/tradecraft/services/settings_catalog.py`, add:

```python
    "crypto_quant_enabled": SettingMeta(
        group="binance",
        label="바이낸스 퀀트 신호 활성화",
        description="ATR, RSI, EMA, volume, spread, funding 기반 롱/숏/관망 패킷을 생성합니다.",
        value_type="bool",
    ),
    "crypto_quant_context_limit": SettingMeta(
        group="binance",
        label="쥬 판단용 퀀트 신호 수",
        description="바이낸스 쥬 프롬프트에 넣을 최신 퀀트 신호 최대 개수입니다.",
        value_type="int",
        min_value=4,
        max_value=50,
    ),
```

If the local `SettingMeta` signature uses different field names, match nearby Binance setting entries exactly.

- [ ] **Step 4: Run settings test**

Run:

```bash
pytest tests/test_config.py::test_crypto_quant_settings_are_visible -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/services/settings_catalog.py tests/test_config.py
git commit -m "feat: add crypto quant settings"
```

---

### Task 9: Regression And Runtime Verification

**Files:**
- No code changes unless verification reveals failures.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
pytest tests/test_crypto_quant.py tests/test_crypto_market_research.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_api_smoke.py::test_crypto_quant_signals_api_requires_admin -q
```

Expected: PASS.

- [ ] **Step 2: Run syntax checks**

Run:

```bash
node --check src/tradecraft/web/static/app.js
git diff --check
```

Expected: both PASS with no output from `git diff --check`.

- [ ] **Step 3: Run one local quant generation smoke**

Run:

```bash
python3 - <<'PY'
from tradecraft.services.crypto_quant import CryptoQuantEngine

bars = [
    {"open": 100+i, "high": 102+i, "low": 99+i, "close": 101+i, "volume": 1000+i*20}
    for i in range(30)
]
signal = CryptoQuantEngine().build_signal(
    symbol="BTCUSDT",
    horizon="intraday",
    klines_by_interval={"15m": bars, "1h": bars},
    market_features={"spread_bps": 1.0, "funding_rate": 0.0001, "mark_index_basis_pct": 0.02},
)
print(signal["symbol"], signal["bias"], signal["long_score"], signal["short_score"], signal["no_trade_score"])
PY
```

Expected: prints one line like `BTCUSDT long 60.0 35.0 20.0`.

- [ ] **Step 4: Restart local services**

Run the project’s existing local service restart command or the same launch method currently used for:

```bash
hermes-crypto-alpha
hermes-binance-block-trader
hermes-control
```

Expected: services restart without crash.

- [ ] **Step 5: Verify API**

Run:

```bash
curl -s http://127.0.0.1:18080/api/health
```

Expected: JSON with `"ok": true` or the existing healthy status field.

- [ ] **Step 6: Commit verification fixes if any**

If verification required fixes:

```bash
git add <fixed-files>
git commit -m "fix: stabilize binance quant engine"
```

If no fixes were needed, do not create an empty commit.

---

## Acceptance Criteria

- Binance Jue receives `crypto_quant` in every manager run when quant DB exists.
- Quant packet has separate `long_score`, `short_score`, `no_trade_score`, `expected_r_long`, `expected_r_short`.
- Quant math includes at least ATR%, RSI, EMA slope, volume z-score, spread, funding, basis, BTC/ETH correlation fields.
- Raw market series remain in `crypto_klines` and `crypto_market_snapshots`; derived quant signals are stored independently in `.runtime/crypto_quant.db`.
- `.runtime/crypto_quant.db` keeps both latest lookup rows and append-only `crypto_quant_signal_history` rows, so Jue can retrieve recent score/bias changes for relevant symbols.
- Binance Jue prompt includes a compact `crypto_quant.history` retrieval context, not the whole DB dump.
- UI displays a Binance quant board with bias and score columns.
- Outcome labeler can determine target-first, stop-first, MFE, MAE, and R multiple.
- Existing execution safety gates remain unchanged.
- No strategy hard filter is introduced beyond existing deterministic safety gates.

## Self-Review

- Spec coverage: The plan covers quant feature computation, directional scoring, prompt injection, API/UI visibility, and outcome labeling.
- Placeholder scan: No task uses TBD/TODO or unspecified “add tests”; each task includes concrete test snippets and commands.
- Type consistency: `CryptoQuantConfig`, `CryptoQuantRepository`, `CryptoQuantEngine`, and `CryptoQuantOutcomeLabeler` are introduced before downstream tasks use them.
- Risk note: Task 3 may need minor adjustment to match the exact local `collect_once` variable names. The intended seam is clear: after each snapshot/features payload is built and saved, save quant signals from the same data.
