# Binance Jue Trading Edge V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Binance Jue from a single-timeframe crypto research assistant into a risk-aware, self-evaluating Binance block trading partner.

**Architecture:** Keep Binance Jue separate from KIS Jue. Extend `CryptoMarketResearchService` to collect multi-timeframe market structure, market regime, and futures squeeze risk; then feed only compact top-k context into `BinanceBlockTrader`. Add a Binance-specific risk sizing layer and performance feedback loop so Spark decisions are bounded by account risk and evaluated after every block.

**Tech Stack:** Python 3.10, FastAPI, sqlite3, existing `BinanceAdapter`, existing `CodexNativeRuntime`, static frontend (`index.html`, `app.js`, `style.css`), pytest.

---

## Brainstorming Summary

### Selected Approach: Wide Scan, Deep Focus, Hard Risk Gate

Binance Jue should not try to become profitable by sending more symbols directly to Spark. It should scan broadly with deterministic feature extraction, compress the highest-signal symbols, and let Spark reason only over that compact packet. Actual order sizing must not be left purely to prose; Spark may propose intent, but risk budget code decides final quantity.

### Six Required Improvements

1. Multi-timeframe context: add `1m`, `5m`, `15m`, `1h`, `4h` structure.
2. Entry quality: distinguish “direction looks good” from “entry is good now”.
3. Position sizing: calculate quantity from account risk, stop distance, leverage, and exposure.
4. Performance feedback: record MFE/MAE, R multiple, win rate, and pattern scorecards.
5. Futures squeeze risk: detect crowded shorts/longs using funding, basis, OI, and market breadth.
6. Market regime: classify BTC-led trend, alt weakness, risk-off washout, squeeze-prone chop, and rotation.

### Explicit Non-Goals

- Do not merge Binance Jue memory into KIS Jue.
- Do not enable live Binance orders by default.
- Do not add a huge new third-party dependency.
- Do not send 50-100 raw symbols to Spark.
- Do not create hard strategy bans. Use risk gates and scorecards.

---

## File Structure

- Modify `src/tradecraft/config.py`
  - Add Binance research/risk config defaults.
- Modify `src/tradecraft/services/binance.py`
  - Reuse existing kline/ticker helpers; add no new auth requirement for public data.
- Modify `src/tradecraft/services/crypto_market_research.py`
  - Add multi-timeframe features, market regime snapshots, squeeze risk, focus ranking.
- Create `src/tradecraft/services/binance_risk.py`
  - Calculate block quantity and exposure guard results from account + stop.
- Modify `src/tradecraft/services/binance_block_trader.py`
  - Inject risk sizing, performance context, entry quality, and squeeze guard into manager prompt and action validation.
- Modify `src/tradecraft/runtime/crypto_market_research_runner.py`
  - Keep wide scan cadence; store compact state.
- Modify `src/tradecraft/runtime/binance_block_trader_runner.py`
  - Wire `BinanceRiskSizer`.
- Modify `src/tradecraft/main.py`
  - Expose risk/performance fields in status APIs.
- Modify `src/tradecraft/web/static/app.js`
  - Show regime, timeframe matrix, risk budget, and performance cards.
- Modify `src/tradecraft/web/static/style.css`
  - Add compact dark dashboard styles for Binance edge panels.
- Tests:
  - `tests/test_crypto_market_research.py`
  - `tests/test_binance_risk.py`
  - `tests/test_binance_block_trader.py`
  - `tests/test_binance_block_trader_runner.py`
  - `tests/test_binance_trader_api.py`
  - `tests/test_config.py`

---

## Task 1: Config Defaults For Wide Research And Risk

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing config test**

Add to `tests/test_config.py`:

```python
def test_binance_jue_edge_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TRADECRAFT_CRYPTO_MARKET_RESEARCH_KLINE_INTERVALS", raising=False)
    monkeypatch.delenv("TRADECRAFT_BINANCE_BLOCK_TRADER_ACCOUNT_RISK_PCT", raising=False)

    settings = AppSettings(_env_file=None)

    assert settings.crypto_market_research_kline_intervals == "1m:120,5m:96,15m:96,1h:168,4h:180"
    assert settings.crypto_market_research_llm_top_symbols == 15
    assert settings.crypto_market_research_regime_enabled is True
    assert settings.crypto_market_research_squeeze_guard_enabled is True
    assert settings.binance_block_trader_account_risk_pct == 0.25
    assert settings.binance_block_trader_max_total_exposure_usdt == 0.0
    assert settings.binance_block_trader_max_symbol_exposure_pct == 25.0
    assert settings.binance_block_trader_min_reward_risk == 1.3
```

- [ ] **Step 2: Run the test and verify red**

Run:

```bash
pytest tests/test_config.py::test_binance_jue_edge_defaults -q
```

Expected: fail with missing `AppSettings` attributes.

- [ ] **Step 3: Add settings**

Add these fields near current crypto/Binance settings in `src/tradecraft/config.py`:

```python
crypto_market_research_kline_intervals: str = Field(
    default="1m:120,5m:96,15m:96,1h:168,4h:180",
    alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_KLINE_INTERVALS",
)
crypto_market_research_regime_enabled: bool = Field(
    default=True,
    alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_REGIME_ENABLED",
)
crypto_market_research_squeeze_guard_enabled: bool = Field(
    default=True,
    alias="TRADECRAFT_CRYPTO_MARKET_RESEARCH_SQUEEZE_GUARD_ENABLED",
)
binance_block_trader_account_risk_pct: float = Field(
    default=0.25,
    alias="TRADECRAFT_BINANCE_BLOCK_TRADER_ACCOUNT_RISK_PCT",
)
binance_block_trader_max_total_exposure_usdt: float = Field(
    default=0.0,
    alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MAX_TOTAL_EXPOSURE_USDT",
)
binance_block_trader_max_symbol_exposure_pct: float = Field(
    default=25.0,
    alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MAX_SYMBOL_EXPOSURE_PCT",
)
binance_block_trader_min_reward_risk: float = Field(
    default=1.3,
    alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MIN_REWARD_RISK",
)
```

- [ ] **Step 4: Update `.env.example`**

Add:

```bash
TRADECRAFT_CRYPTO_MARKET_RESEARCH_KLINE_INTERVALS=1m:120,5m:96,15m:96,1h:168,4h:180
TRADECRAFT_CRYPTO_MARKET_RESEARCH_REGIME_ENABLED=true
TRADECRAFT_CRYPTO_MARKET_RESEARCH_SQUEEZE_GUARD_ENABLED=true
TRADECRAFT_BINANCE_BLOCK_TRADER_ACCOUNT_RISK_PCT=0.25
TRADECRAFT_BINANCE_BLOCK_TRADER_MAX_TOTAL_EXPOSURE_USDT=0
TRADECRAFT_BINANCE_BLOCK_TRADER_MAX_SYMBOL_EXPOSURE_PCT=25
TRADECRAFT_BINANCE_BLOCK_TRADER_MIN_REWARD_RISK=1.3
```

- [ ] **Step 5: Run config test**

Run:

```bash
pytest tests/test_config.py::test_binance_jue_edge_defaults -q
```

Expected: pass.

---

## Task 2: Multi-Timeframe Crypto Features

**Files:**
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_crypto_market_research.py`

- [ ] **Step 1: Write the failing test**

Add:

```python
def test_collect_builds_multi_timeframe_features(tmp_path: Path) -> None:
    class MultiFrameBinance(_FakeBinance):
        async def fetch_klines(
            self,
            symbol: str,
            *,
            market: str = "spot",
            interval: str = "1m",
            limit: int = 120,
        ) -> list[dict[str, Any]]:
            closes = {
                "1m": [100, 101, 102, 103],
                "5m": [100, 99, 98, 97],
                "15m": [100, 100, 100, 100],
                "1h": [100, 105, 110, 115],
                "4h": [120, 115, 110, 105],
            }[interval]
            return [
                {
                    "open_time": idx,
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 10,
                    "quote_volume": 1000,
                    "close_time": idx + 1,
                    "raw": [],
                }
                for idx, close in enumerate(closes)
            ]

    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(
            db_path=str(tmp_path / "crypto.db"),
            kline_intervals={"1m": 4, "5m": 4, "15m": 4, "1h": 4, "4h": 4},
        ),
        binance=MultiFrameBinance(),
    )

    result = asyncio.run(service.collect_market_structure(["BTCUSDT"]))
    context = service.latest_context(limit=5)
    features = context["items"][0]["features"]

    assert result["status"] == "ok"
    assert features["timeframes"]["1m"]["trend"] == "up"
    assert features["timeframes"]["5m"]["trend"] == "down"
    assert features["timeframes"]["15m"]["trend"] == "flat"
    assert features["timeframes"]["1h"]["momentum_pct"] == pytest.approx(15.0)
    assert features["timeframe_alignment"] in {"mixed", "bullish", "bearish"}
```

- [ ] **Step 2: Run and verify red**

```bash
pytest tests/test_crypto_market_research.py::test_collect_builds_multi_timeframe_features -q
```

Expected: fail because `kline_intervals` and `timeframes` do not exist.

- [ ] **Step 3: Add config field to dataclass**

In `CryptoMarketResearchConfig`:

```python
kline_intervals: dict[str, int] | None = None
```

Add helper:

```python
def _resolved_kline_intervals(self) -> dict[str, int]:
    return self.config.kline_intervals or {
        "1m": 120,
        "5m": 96,
        "15m": 96,
        "1h": 168,
        "4h": 180,
    }
```

- [ ] **Step 4: Collect klines per interval**

Replace single `klines_1m` collection with:

```python
klines_by_interval: dict[str, list[dict[str, Any]]] = {}
for interval, limit in self._resolved_kline_intervals().items():
    rows = await self.binance.fetch_klines(
        symbol,
        market="spot",
        interval=interval,
        limit=limit,
    )
    klines_by_interval[interval] = rows
    self.repository.save_klines(
        symbol=symbol,
        market="spot",
        interval=interval,
        rows=rows,
    )
```

Return:

```python
"klines_by_interval": klines_by_interval,
"klines_1m": klines_by_interval.get("1m", []),
```

- [ ] **Step 5: Build timeframe features**

Add:

```python
def _timeframe_feature(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [_to_float(row.get("close")) for row in rows if isinstance(row, dict)]
    closes = [value for value in closes if value > 0]
    if len(closes) < 2:
        return {"trend": "unknown", "momentum_pct": 0.0, "bar_count": len(closes)}
    first = closes[0]
    last = closes[-1]
    momentum_pct = (last - first) / first * 100.0 if first > 0 else 0.0
    if momentum_pct > 0.15:
        trend = "up"
    elif momentum_pct < -0.15:
        trend = "down"
    else:
        trend = "flat"
    return {
        "trend": trend,
        "momentum_pct": momentum_pct,
        "bar_count": len(closes),
        "first_close": first,
        "last_close": last,
    }
```

In `_build_features`, add:

```python
timeframes = {
    interval: self._timeframe_feature(rows)
    for interval, rows in _dict_or_empty(snapshot.get("klines_by_interval")).items()
    if isinstance(rows, list)
}
trends = [str(row.get("trend")) for row in timeframes.values()]
up_count = trends.count("up")
down_count = trends.count("down")
if up_count >= 3:
    alignment = "bullish"
elif down_count >= 3:
    alignment = "bearish"
else:
    alignment = "mixed"
features["timeframes"] = timeframes
features["timeframe_alignment"] = alignment
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_crypto_market_research.py::test_collect_builds_multi_timeframe_features -q
```

Expected: pass.

---

## Task 3: Market Regime Snapshot

**Files:**
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_crypto_market_research.py`

- [ ] **Step 1: Write the failing test**

Add:

```python
def test_market_regime_uses_btc_and_breadth(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )
    for symbol, change, alignment in [
        ("BTCUSDT", -4.0, "bearish"),
        ("ETHUSDT", -5.0, "bearish"),
        ("SOLUSDT", -6.0, "bearish"),
        ("BNBUSDT", -1.0, "mixed"),
    ]:
        service.repository.upsert_features(
            symbol,
            {
                "symbol": symbol,
                "change_pct_24h": change,
                "quote_volume_usdt": 10_000_000,
                "timeframe_alignment": alignment,
            },
        )

    regime = service.compute_market_regime(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])

    assert regime["status"] == "ok"
    assert regime["regime"] == "risk_off_downtrend"
    assert regime["btc_change_pct_24h"] == pytest.approx(-4.0)
    assert regime["bearish_breadth_pct"] >= 75.0
```

- [ ] **Step 2: Run and verify red**

```bash
pytest tests/test_crypto_market_research.py::test_market_regime_uses_btc_and_breadth -q
```

Expected: fail with missing `compute_market_regime`.

- [ ] **Step 3: Add repository table**

In `_ensure_schema`, add:

```sql
CREATE TABLE IF NOT EXISTS crypto_regime_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    regime TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crypto_regime_snapshots_captured
    ON crypto_regime_snapshots(captured_at DESC);
```

Add method:

```python
def save_regime_snapshot(self, payload: dict[str, Any]) -> int:
    with self._connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO crypto_regime_snapshots (regime, payload_json, captured_at)
            VALUES (?, ?, ?)
            """,
            (
                str(payload.get("regime") or ""),
                json_dumps(payload),
                utc_now_iso(),
            ),
        )
        return int(cur.lastrowid)
```

- [ ] **Step 4: Add regime computation**

```python
def compute_market_regime(self, *, symbols: list[str]) -> dict[str, Any]:
    rows = self.repository.latest_features(symbols=symbols, limit=max(len(symbols), 1))
    if not rows:
        return {"status": "missing", "regime": "unknown"}
    feature_by_symbol = {str(row["symbol"]): row["features"] for row in rows}
    btc = feature_by_symbol.get("BTCUSDT", {})
    bearish = 0
    bullish = 0
    for feature in feature_by_symbol.values():
        alignment = str(feature.get("timeframe_alignment") or "")
        change = _to_float(feature.get("change_pct_24h"))
        if alignment == "bearish" or change <= -4.0:
            bearish += 1
        if alignment == "bullish" or change >= 4.0:
            bullish += 1
    total = max(len(feature_by_symbol), 1)
    bearish_pct = bearish / total * 100.0
    bullish_pct = bullish / total * 100.0
    btc_change = _to_float(btc.get("change_pct_24h"))
    if btc_change <= -3.0 and bearish_pct >= 60.0:
        regime = "risk_off_downtrend"
    elif btc_change >= 3.0 and bullish_pct >= 50.0:
        regime = "btc_led_risk_on"
    elif bearish_pct >= 45.0 and bullish_pct >= 25.0:
        regime = "high_dispersion_chop"
    else:
        regime = "mixed_rotation"
    payload = {
        "status": "ok",
        "regime": regime,
        "btc_change_pct_24h": btc_change,
        "bearish_breadth_pct": bearish_pct,
        "bullish_breadth_pct": bullish_pct,
        "symbol_count": total,
    }
    self.repository.save_regime_snapshot(payload)
    return payload
```

- [ ] **Step 5: Include regime in research packet**

In `_research_packet`:

```python
regime = self.compute_market_regime(symbols=observed_symbols) if observed_symbols else {"status": "missing"}
```

Add to return:

```python
"market_regime": regime,
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_crypto_market_research.py::test_market_regime_uses_btc_and_breadth -q
```

Expected: pass.

---

## Task 4: Futures Squeeze Risk Feature

**Files:**
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_crypto_market_research.py`

- [ ] **Step 1: Write the failing test**

Add:

```python
def test_squeeze_risk_scores_crowded_short(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )
    feature = service._squeeze_risk_feature(
        {
            "funding_rate": -0.0012,
            "mark_index_basis_pct": -0.25,
            "open_interest": 2_000_000,
            "trend_1m": "down",
        }
    )

    assert feature["squeeze_risk"] in {"short_squeeze", "high_short_squeeze"}
    assert feature["squeeze_risk_score"] >= 70
    assert "negative funding" in " ".join(feature["squeeze_risk_reasons"])
```

- [ ] **Step 2: Run and verify red**

```bash
pytest tests/test_crypto_market_research.py::test_squeeze_risk_scores_crowded_short -q
```

Expected: fail with missing `_squeeze_risk_feature`.

- [ ] **Step 3: Implement squeeze scoring**

Add:

```python
def _squeeze_risk_feature(self, features: dict[str, Any]) -> dict[str, Any]:
    funding = _to_float(features.get("funding_rate"))
    basis = _to_float(features.get("mark_index_basis_pct"))
    open_interest = _to_float(features.get("open_interest"))
    trend = str(features.get("trend_1m") or "")
    score = 0.0
    reasons: list[str] = []
    if funding <= -0.0005:
        score += 35
        reasons.append("negative funding indicates crowded shorts")
    if basis <= -0.10:
        score += 25
        reasons.append("mark below index indicates bearish crowding")
    if open_interest >= 1_000_000:
        score += 20
        reasons.append("open interest is elevated")
    if trend == "down":
        score += 10
        reasons.append("short-term trend aligns with crowding")
    if score >= 80:
        label = "high_short_squeeze"
    elif score >= 60:
        label = "short_squeeze"
    elif score <= 20 and funding >= 0.0005 and basis >= 0.10:
        label = "long_squeeze"
    else:
        label = "normal"
    return {
        "squeeze_risk": label,
        "squeeze_risk_score": min(score, 100.0),
        "squeeze_risk_reasons": reasons,
    }
```

In `_build_features`, after base features:

```python
features.update(self._squeeze_risk_feature(features))
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_crypto_market_research.py::test_squeeze_risk_scores_crowded_short tests/test_crypto_market_research.py::test_collect_phase1_market_structure_builds_features -q
```

Expected: pass.

---

## Task 5: Entry Quality And Candidate Ranking

**Files:**
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_crypto_market_research.py`

- [ ] **Step 1: Write failing test**

Add:

```python
def test_entry_quality_penalizes_chasing_extended_move(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )

    quality = service._entry_quality_feature(
        {
            "timeframes": {
                "1m": {"momentum_pct": 4.0, "trend": "up"},
                "5m": {"momentum_pct": 8.0, "trend": "up"},
                "15m": {"momentum_pct": 12.0, "trend": "up"},
            },
            "spread_bps": 1.0,
            "squeeze_risk_score": 20.0,
        }
    )

    assert quality["entry_quality"] == "wait_pullback"
    assert quality["entry_quality_score"] < 60
```

- [ ] **Step 2: Run and verify red**

```bash
pytest tests/test_crypto_market_research.py::test_entry_quality_penalizes_chasing_extended_move -q
```

Expected: fail with missing method.

- [ ] **Step 3: Implement entry quality**

Add:

```python
def _entry_quality_feature(self, features: dict[str, Any]) -> dict[str, Any]:
    timeframes = _dict_or_empty(features.get("timeframes"))
    momentum_values = [
        abs(_to_float(row.get("momentum_pct")))
        for row in timeframes.values()
        if isinstance(row, dict)
    ]
    max_momentum = max(momentum_values or [0.0])
    spread = _to_float(features.get("spread_bps"))
    squeeze_score = _to_float(features.get("squeeze_risk_score"))
    score = 70.0
    reasons: list[str] = []
    if max_momentum >= 10.0:
        score -= 25
        reasons.append("move extended across timeframes")
    if spread >= 8.0:
        score -= 15
        reasons.append("spread is wide")
    if squeeze_score >= 70.0:
        score -= 20
        reasons.append("squeeze risk is elevated")
    if score >= 70:
        label = "actionable_now"
    elif score >= 55:
        label = "conditional"
    else:
        label = "wait_pullback"
    return {
        "entry_quality": label,
        "entry_quality_score": max(min(score, 100.0), 0.0),
        "entry_quality_reasons": reasons,
    }
```

In `_build_features`:

```python
features.update(self._entry_quality_feature(features))
```

- [ ] **Step 4: Ensure Spark prompt uses entry quality**

In `_build_research_prompt`, add this instruction under `task` or `policy`:

```python
"candidate_policy": [
    "Do not treat direction as entry. entry_quality must decide actionable_now, conditional, or wait_pullback.",
    "If entry_quality is wait_pullback, produce block_template.entry_style='wait_pullback'.",
]
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_crypto_market_research.py::test_entry_quality_penalizes_chasing_extended_move tests/test_crypto_market_research.py::test_spark_research_focuses_on_top_ranked_symbols -q
```

Expected: pass.

---

## Task 6: Binance Risk Sizer

**Files:**
- Create: `src/tradecraft/services/binance_risk.py`
- Test: `tests/test_binance_risk.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_binance_risk.py`:

```python
from __future__ import annotations

import pytest

from tradecraft.services.binance_risk import BinanceRiskConfig, BinanceRiskSizer


def test_risk_sizer_calculates_qty_from_stop_distance() -> None:
    sizer = BinanceRiskSizer(
        BinanceRiskConfig(
            account_risk_pct=0.25,
            max_symbol_exposure_pct=25.0,
            min_reward_risk=1.3,
        )
    )

    result = sizer.size_block(
        symbol="BTCUSDT",
        account_equity_usdt=10_000,
        current_symbol_exposure_usdt=0,
        entry_price=50_000,
        stop_price=49_500,
        target_price=51_000,
        side="long",
        proposed_qty=None,
        leverage=1,
    )

    assert result["status"] == "ok"
    assert result["risk_budget_usdt"] == pytest.approx(25.0)
    assert result["qty"] == pytest.approx(0.05)
    assert result["reward_risk"] == pytest.approx(2.0)


def test_risk_sizer_rejects_bad_reward_risk() -> None:
    sizer = BinanceRiskSizer(BinanceRiskConfig(account_risk_pct=0.25, min_reward_risk=1.3))

    result = sizer.size_block(
        symbol="BTCUSDT",
        account_equity_usdt=10_000,
        current_symbol_exposure_usdt=0,
        entry_price=50_000,
        stop_price=49_500,
        target_price=50_300,
        side="long",
        proposed_qty=None,
        leverage=1,
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "reward_risk_too_low"
```

- [ ] **Step 2: Run and verify red**

```bash
pytest tests/test_binance_risk.py -q
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement risk sizer**

Create `src/tradecraft/services/binance_risk.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass(slots=True)
class BinanceRiskConfig:
    account_risk_pct: float = 0.25
    max_total_exposure_usdt: float = 0.0
    max_symbol_exposure_pct: float = 25.0
    min_reward_risk: float = 1.3


class BinanceRiskSizer:
    def __init__(self, config: BinanceRiskConfig) -> None:
        self.config = config

    def size_block(
        self,
        *,
        symbol: str,
        account_equity_usdt: float,
        current_symbol_exposure_usdt: float,
        entry_price: float,
        stop_price: float,
        target_price: float,
        side: str,
        proposed_qty: float | None,
        leverage: int = 1,
    ) -> dict[str, Any]:
        equity = _to_float(account_equity_usdt)
        entry = _to_float(entry_price)
        stop = _to_float(stop_price)
        target = _to_float(target_price)
        if equity <= 0 or entry <= 0 or stop <= 0 or target <= 0:
            return {"status": "rejected", "reason": "missing_price_or_equity", "symbol": symbol}
        stop_distance = abs(entry - stop)
        reward_distance = abs(target - entry)
        if stop_distance <= 0:
            return {"status": "rejected", "reason": "invalid_stop_distance", "symbol": symbol}
        reward_risk = reward_distance / stop_distance
        if reward_risk < self.config.min_reward_risk:
            return {
                "status": "rejected",
                "reason": "reward_risk_too_low",
                "symbol": symbol,
                "reward_risk": reward_risk,
            }
        risk_budget = equity * (self.config.account_risk_pct / 100.0)
        max_qty_by_risk = risk_budget / stop_distance
        max_symbol_exposure = equity * (self.config.max_symbol_exposure_pct / 100.0)
        remaining_symbol_exposure = max(max_symbol_exposure - _to_float(current_symbol_exposure_usdt), 0.0)
        max_qty_by_exposure = remaining_symbol_exposure / entry if max_symbol_exposure > 0 else max_qty_by_risk
        raw_qty = min(max_qty_by_risk, max_qty_by_exposure)
        if proposed_qty is not None and proposed_qty > 0:
            raw_qty = min(raw_qty, proposed_qty)
        if raw_qty <= 0:
            return {"status": "rejected", "reason": "exposure_budget_exhausted", "symbol": symbol}
        return {
            "status": "ok",
            "symbol": symbol,
            "side": side,
            "qty": raw_qty,
            "risk_budget_usdt": risk_budget,
            "stop_distance_usdt": stop_distance,
            "reward_risk": reward_risk,
            "notional_usdt": raw_qty * entry,
            "leverage": max(int(leverage or 1), 1),
        }
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_binance_risk.py -q
```

Expected: pass.

---

## Task 7: Wire Risk Sizer Into Binance Block Manager

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/runtime/binance_block_trader_runner.py`
- Test: `tests/test_binance_block_trader.py`
- Test: `tests/test_binance_block_trader_runner.py`

- [ ] **Step 1: Write failing block trader test**

Add to `tests/test_binance_block_trader.py`:

```python
def test_manager_create_block_uses_risk_sizer_quantity(tmp_path: Path) -> None:
    class FixedRiskSizer:
        def size_block(self, **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok", "qty": 0.05, "risk_budget_usdt": 25.0, "reward_risk": 2.0}

    trader = _make_trader(
        tmp_path,
        llm=_FakeLLM(
            {
                "create_blocks": [
                    {
                        "symbol": "BTCUSDT",
                        "market": "spot",
                        "side": "long",
                        "entry_price": 50000,
                        "target_price": 51000,
                        "stop_price": 49500,
                        "qty": 9.0,
                        "thesis": "test",
                    }
                ]
            }
        ),
    )
    trader.risk_sizer = FixedRiskSizer()

    result = asyncio.run(trader.run_manager_once(universe=["BTCUSDT"]))
    block = trader.repository.list_blocks(include_closed=False)[0]

    assert result["status"] == "ok"
    assert block["qty_initial"] == pytest.approx(0.05)
    assert block["metadata"]["risk_budget_usdt"] == pytest.approx(25.0)
```

- [ ] **Step 2: Run and verify red**

```bash
pytest tests/test_binance_block_trader.py::test_manager_create_block_uses_risk_sizer_quantity -q
```

Expected: fail because trader does not use `risk_sizer`.

- [ ] **Step 3: Add constructor parameter**

In `BinanceBlockTrader.__init__`:

```python
risk_sizer: Any | None = None,
```

Set:

```python
self.risk_sizer = risk_sizer
```

- [ ] **Step 4: Apply risk sizing before create**

In create block action path, before `_normalize_block_payload`, call:

```python
payload = self._apply_risk_sizing(payload)
```

Add:

```python
def _apply_risk_sizing(self, payload: dict[str, Any]) -> dict[str, Any]:
    if self.risk_sizer is None:
        return payload
    entry = _safe_float(payload.get("entry_price") or payload.get("entry_price_usdt"))
    stop = _safe_float(payload.get("stop_price") or payload.get("stop_price_usdt"))
    target = _safe_float(payload.get("target_price") or payload.get("target_price_usdt"))
    if entry <= 0 or stop <= 0 or target <= 0:
        return payload
    account = getattr(self, "_last_account_snapshot", {}) or {}
    equity = (
        _safe_float(account.get("spot_cash_usdt"))
        + _safe_float(account.get("futures_cash_usdt"))
    )
    result = self.risk_sizer.size_block(
        symbol=str(payload.get("symbol") or ""),
        account_equity_usdt=equity,
        current_symbol_exposure_usdt=0.0,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        side=str(payload.get("side") or "long"),
        proposed_qty=_safe_float(payload.get("qty")),
        leverage=int(_safe_float(payload.get("leverage")) or 1),
    )
    if result.get("status") != "ok":
        raise ValueError(f"risk_sizer_rejected:{result.get('reason')}")
    out = dict(payload)
    out["qty"] = result["qty"]
    metadata = _json_dict(out.get("metadata") or {})
    metadata["risk_sizing"] = result
    out["metadata"] = metadata
    return out
```

If `_last_account_snapshot` does not exist, set it in `run_manager_once` after account collection:

```python
account = await self._collect_account_snapshot()
self._last_account_snapshot = account
```

Use `account` in prompt instead of calling twice.

- [ ] **Step 5: Wire runner**

In `src/tradecraft/runtime/binance_block_trader_runner.py`, import:

```python
from tradecraft.services.binance_risk import BinanceRiskConfig, BinanceRiskSizer
```

Construct:

```python
risk_sizer = BinanceRiskSizer(
    BinanceRiskConfig(
        account_risk_pct=settings.binance_block_trader_account_risk_pct,
        max_total_exposure_usdt=settings.binance_block_trader_max_total_exposure_usdt,
        max_symbol_exposure_pct=settings.binance_block_trader_max_symbol_exposure_pct,
        min_reward_risk=settings.binance_block_trader_min_reward_risk,
    )
)
```

Pass to `BinanceBlockTrader(..., risk_sizer=risk_sizer)`.

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_binance_risk.py tests/test_binance_block_trader.py::test_manager_create_block_uses_risk_sizer_quantity tests/test_binance_block_trader_runner.py::test_build_trader_wires_crypto_research_provider -q
```

Expected: pass.

---

## Task 8: Performance Feedback And Scorecards

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing reflection test**

Add:

```python
def test_closed_block_generates_performance_reflection(tmp_path: Path) -> None:
    trader = _make_trader(tmp_path)
    block = trader.create_block(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "side": "long",
            "qty": 0.1,
            "entry_price": 100,
            "target_price": 110,
            "stop_price": 95,
            "status": "open",
            "thesis": "trend pullback",
        }
    )
    trader.repository.update_block_status(block["block_id"], "closed")
    trader.repository.record_event(
        block["block_id"],
        "closed",
        payload={"exit_price": 108, "reason": "target_near"},
    )

    result = trader.run_performance_feedback_once()

    assert result["status"] == "ok"
    assert result["reflection_count"] == 1
    detail = trader.block_detail(block["block_id"])
    assert detail["performance_reflection"]["r_multiple"] == pytest.approx(1.6)
```

- [ ] **Step 2: Run and verify red**

```bash
pytest tests/test_binance_block_trader.py::test_closed_block_generates_performance_reflection -q
```

Expected: fail with missing method/table.

- [ ] **Step 3: Add table**

In Binance repository schema:

```sql
CREATE TABLE IF NOT EXISTS block_performance_reflections (
    block_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'spot',
    side TEXT NOT NULL DEFAULT 'long',
    entry_price REAL NOT NULL DEFAULT 0,
    exit_price REAL NOT NULL DEFAULT 0,
    stop_price REAL NOT NULL DEFAULT 0,
    target_price REAL NOT NULL DEFAULT 0,
    pnl_usdt REAL NOT NULL DEFAULT 0,
    r_multiple REAL NOT NULL DEFAULT 0,
    lesson_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
```

- [ ] **Step 4: Implement reflection calculation**

Add:

```python
def run_performance_feedback_once(self) -> dict[str, Any]:
    closed = [
        block for block in self.repository.list_blocks(include_closed=True)
        if block.get("status") == "closed"
    ]
    count = 0
    for block in closed:
        if self.repository.get_performance_reflection(block["block_id"]):
            continue
        entry = _safe_float(block.get("entry_price"))
        stop = _safe_float(block.get("stop_price"))
        target = _safe_float(block.get("target_price"))
        events = self.repository.list_events(block["block_id"])
        close_events = [event for event in events if event.get("event_type") == "closed"]
        exit_price = _safe_float((close_events[-1].get("payload") or {}).get("exit_price")) if close_events else 0.0
        if entry <= 0 or stop <= 0 or exit_price <= 0:
            continue
        risk = abs(entry - stop)
        pnl_per_unit = exit_price - entry if block.get("side") == "long" else entry - exit_price
        r_multiple = pnl_per_unit / risk if risk > 0 else 0.0
        self.repository.save_performance_reflection(
            {
                "block_id": block["block_id"],
                "symbol": block["symbol"],
                "market": block["market"],
                "side": block["side"],
                "entry_price": entry,
                "exit_price": exit_price,
                "stop_price": stop,
                "target_price": target,
                "r_multiple": r_multiple,
                "lesson": {
                    "thesis": block.get("thesis", ""),
                    "result": "positive" if r_multiple > 0 else "negative",
                },
            }
        )
        count += 1
    return {"status": "ok", "reflection_count": count}
```

- [ ] **Step 5: Add scorecard to manager prompt**

Add repository method `latest_performance_scorecard(limit=20)` returning:

```python
{
    "sample_count": n,
    "avg_r_multiple": avg,
    "win_rate_pct": win_rate,
    "recent_lessons": lessons,
}
```

In `run_manager_once`, add:

```python
"performance": self.repository.latest_performance_scorecard(limit=20),
```

Add `"performance"` to `decision_inputs`.

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_binance_block_trader.py::test_closed_block_generates_performance_reflection -q
```

Expected: pass.

---

## Task 9: API And UI Exposure

**Files:**
- Modify: `src/tradecraft/main.py`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `tests/test_binance_trader_api.py`

- [ ] **Step 1: Write failing API test**

Add to `tests/test_binance_trader_api.py`:

```python
def test_binance_status_includes_research_edge_fields(monkeypatch) -> None:
    class FakeTrader:
        def status(self) -> dict:
            return {
                "status": "ok",
                "risk": {"account_risk_pct": 0.25},
                "performance": {"sample_count": 3, "avg_r_multiple": 0.4},
            }

    monkeypatch.setattr(main, "binance_block_trader", FakeTrader())

    with TestClient(main.app) as client:
        response = client.get("/api/binance/blocks/status", headers=_admin_headers(monkeypatch))

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk"]["account_risk_pct"] == 0.25
    assert payload["performance"]["sample_count"] == 3
```

- [ ] **Step 2: Run and verify red if needed**

```bash
pytest tests/test_binance_trader_api.py::test_binance_status_includes_research_edge_fields -q
```

Expected: fail if route strips fields.

- [ ] **Step 3: Extend status payload**

In `BinanceBlockTrader.status()`, include:

```python
"risk": {
    "account_risk_pct": getattr(self.risk_sizer.config, "account_risk_pct", 0.0) if self.risk_sizer else 0.0,
    "max_symbol_exposure_pct": getattr(self.risk_sizer.config, "max_symbol_exposure_pct", 0.0) if self.risk_sizer else 0.0,
    "min_reward_risk": getattr(self.risk_sizer.config, "min_reward_risk", 0.0) if self.risk_sizer else 0.0,
},
"performance": self.repository.latest_performance_scorecard(limit=20),
```

- [ ] **Step 4: Add UI cards**

In `renderCryptoResearchPanel()` add:

```javascript
const regime = context.market_regime || status.market_regime || {};
const edge = status.edge || {};
```

Render:

```javascript
<article class="mini-card"><p>시장 국면</p><h4>${escapeHTML(regime.regime || "-")}</h4></article>
<article class="mini-card"><p>감시/집중</p><h4>${escapeHTML(`${status.max_symbols || "-"} / ${status.llm_top_symbols || "-"}`)}</h4></article>
```

In Binance tab status area, show:

```javascript
<article class="mini-card"><p>Risk / Block</p><h4>${escapeHTML(fmtPercent(payload.risk?.account_risk_pct || 0, 2))}</h4></article>
<article class="mini-card"><p>Avg R</p><h4>${escapeHTML(fmtNum(payload.performance?.avg_r_multiple || 0, 2))}</h4></article>
```

- [ ] **Step 5: Add CSS**

Add:

```css
.crypto-edge-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}
.crypto-timeframe-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(92px, 1fr));
  gap: 8px;
}
.crypto-timeframe-cell {
  border: 1px solid var(--border-soft);
  background: var(--surface-soft);
  padding: 8px;
  border-radius: 8px;
}
```

- [ ] **Step 6: Run frontend checks**

```bash
node --check src/tradecraft/web/static/app.js
pytest tests/test_binance_trader_api.py::test_binance_status_includes_research_edge_fields -q
```

Expected: pass.

---

## Task 10: Runner State And Cadence Verification

**Files:**
- Modify: `src/tradecraft/runtime/crypto_market_research_runner.py`
- Test: `tests/test_binance_block_trader_runner.py`

- [ ] **Step 1: Write failing runner test**

Extend `test_crypto_market_research_runner_writes_state_once` assertions:

```python
assert payload["universe"]["dynamic_count"] == 2
assert payload["focus_symbol_count"] == 2
assert payload["symbol_count"] == 4
```

- [ ] **Step 2: Run and verify red**

```bash
pytest tests/test_binance_block_trader_runner.py::test_crypto_market_research_runner_writes_state_once -q
```

Expected: fail because state does not include compact counts.

- [ ] **Step 3: Add compact state fields**

Before `snapshot`:

```python
focus_symbols = []
select_focus = getattr(resolved, "select_llm_focus_symbols", None)
if select_focus is not None:
    try:
        focus_symbols = select_focus(symbols=symbols)
    except Exception:
        focus_symbols = []
```

Add to snapshot:

```python
"symbol_count": len(symbols),
"focus_symbols": focus_symbols,
"focus_symbol_count": len(focus_symbols),
```

- [ ] **Step 4: Run test**

```bash
pytest tests/test_binance_block_trader_runner.py::test_crypto_market_research_runner_writes_state_once -q
```

Expected: pass.

---

## Task 11: Full Verification

**Files:**
- No production changes.

- [ ] **Step 1: Run focused tests**

```bash
pytest tests/test_crypto_market_research.py tests/test_binance_risk.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_binance_trader_api.py tests/test_config.py -q
```

Expected: all pass.

- [ ] **Step 2: Run API smoke**

```bash
pytest tests/test_api_smoke.py -q
```

Expected: pass.

- [ ] **Step 3: Run JS syntax check**

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no output, exit code 0.

- [ ] **Step 4: Run lint**

```bash
ruff check src/tradecraft/config.py src/tradecraft/main.py src/tradecraft/runtime/crypto_market_research_runner.py src/tradecraft/runtime/binance_block_trader_runner.py src/tradecraft/services/binance.py src/tradecraft/services/binance_risk.py src/tradecraft/services/binance_block_trader.py src/tradecraft/services/crypto_market_research.py tests/test_crypto_market_research.py tests/test_binance_risk.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_binance_trader_api.py tests/test_config.py
```

Expected: `All checks passed!`

- [ ] **Step 5: Run whitespace check**

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 6: Restart local runtime**

```bash
tmux kill-session -t hermes-crypto-research 2>/dev/null || true
tmux new-session -d -s hermes-crypto-research -c /Users/juhwan/hermes_v2 '.venv/bin/tradecraft-crypto-market-research > .runtime/crypto_market_research.log 2>&1'
tmux kill-session -t hermes-control 2>/dev/null || true
tmux new-session -d -s hermes-control -c /Users/juhwan/hermes_v2 'TRADECRAFT_PORT=18080 .venv/bin/tradecraft-control > .runtime/control.log 2>&1'
sleep 8
curl -sS http://127.0.0.1:18080/api/health
```

Expected health:

```json
{"status":"ok","service":"tradecraft-control","ops_endpoint":"/api/ops/readiness","ops_auth_required":true}
```

---

## Self-Review

- Spec coverage: all six requested improvements map to tasks 2 through 8.
- Type consistency: new config names are snake_case and match env aliases.
- Safety: Binance live order flags remain disabled by default.
- Cost control: wide feature scan is deterministic; Spark receives `llm_top_symbols` only.
- KIS isolation: no KIS files are changed except shared settings UI/API surfaces.
