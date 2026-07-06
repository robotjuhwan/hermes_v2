# Binance Jue 50pct Growth Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Binance Jue growth engine that can pursue an aggressive 50% monthly target through measured expectancy, broad crypto discovery, volatile small-attack lanes, walk-forward optimization, live edge promotion, and strict drawdown governors.

**Architecture:** Keep the current HERMES block-trading architecture. Add a target ledger, richer candidate scoring, lane-specific capital budgets, walk-forward optimization, and live-performance promotion gates around the existing Binance block trader instead of replacing it. The model proposes blocks; deterministic services verify execution geometry, risk, exchange constraints, and live edge before an order can exist.

**Tech Stack:** Python 3.10, FastAPI, SQLite runtime stores, Binance REST APIs, Codex Native runtime, static HTML/CSS/JS frontend, pytest, ruff.

---

## External Evidence Used

- Binance Spot API limits: `exchangeInfo` exposes RAW_REQUESTS, REQUEST_WEIGHT, and ORDERS limiters, so any large universe design must be weight-aware. Source: https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits
- Binance USD-M Futures order limits: new futures orders count against 10-second and 1-minute order counters. Source: https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api
- Binance USD-M Futures open interest endpoint has request weight 1 per symbol. Source: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest
- Binance funding history shares a 500/5min/IP limit with funding info. Source: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History
- Binance funding mechanics are based on premium and interest components and funding events are tied to funding intervals. Source: https://www.binance.com/en/support/faq/detail/360033525031
- Crypto momentum research is mixed and crash-prone; volatility management can improve risk-adjusted outcomes but tail risk remains high. Source: https://link.springer.com/article/10.1007/s11408-025-00474-9
- High-frequency crypto data shows intraday momentum and reversal can both exist, and patterns vary around jumps, liquidity, and macro events. Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4080253
- Cross-sectional crypto momentum appears on short horizons while longer horizons can turn into reversal; crypto market metabolism is faster than traditional assets. Source: https://conference.hse.ru/files/download_file_ex?hash=FAE0AB2DC7A67656E89A0B1CB27D8C7D&id=3B5EE9A5-0B18-458A-9458-B4ED0F6C6664

## Current HERMES State

The current implementation already has the right skeleton:

- `src/tradecraft/services/binance_block_trader.py`
  - independent block ledger
  - spot/futures execution gates
  - prompt budget
  - calculated entry/target/stop plan
  - waiting-entry blocks
  - live authority integration
  - `volatile_attack` lane
- `src/tradecraft/services/crypto_market_research.py`
  - dynamic Binance universe
  - market features, kline features, funding, open interest
  - candidate packets
  - regime brief
- `src/tradecraft/services/crypto_pattern_lab.py`
  - backtest and optimized strategy sets
- `src/tradecraft/services/crypto_quant.py`
  - directional quant packets
- `src/tradecraft/services/live_authority.py`
  - realized live edge gate
- `src/tradecraft/web/static/app.js`
  - Binance block dashboard, universe pipeline, backtest confluence panel

As of this plan, runtime configuration is:

- observe universe target: `300`
- deep research universe target: `80`
- manager candidate limit: `60`
- LLM research focus: `30`
- `volatile_attack` lane enabled
- volatile attack budget multiplier: `0.35`
- volatile attack minimum reward/risk: `2.0`

## Strategy Thesis

Monthly 50% is an extreme target. Compounded over a 30-day crypto month, it requires about 1.36% net growth per calendar day before withdrawals. HERMES should not try to reach that by simply increasing trade count. The target requires four distinct edge sources and a governor that shuts down weak edges quickly:

1. **Volatility-managed momentum:** trend leaders can be traded, but size must shrink as volatility expands.
2. **Intraday reversal after jumps:** large jump events can flip from continuation to mean reversion depending on liquidity, wick structure, and orderbook quality.
3. **Volatile small-attack lane:** high-volatility alts can create outsized payoff, but initial sizing must be small and conditional.
4. **Funding/open-interest squeeze layer:** futures entries should distinguish trend extension from crowded liquidation risk.

The monthly target is therefore a scorekeeping objective, not a hard instruction to trade. Jue must know the target, the required run-rate, and the gap, but block creation still depends on evidence, execution geometry, and live-performance authority.

## Target Operating Model

### Universe Funnel

- `observe_universe`: up to 300 liquid USDT symbols from Binance 24h ticker data.
- `research_universe`: 80 symbols with deep OHLCV/book/funding/OI enrichment.
- `manager_candidates`: 30-60 candidates sent to Jue.
- `trade_candidates`: lane-specific candidates with executable entry/target/stop and risk budget.

### Lane Portfolio

- `core_trend`: large/liquid leaders with clean trend and positive live edge.
- `intraday_reversal`: jump exhaustion, failed breakout, wick-heavy reversal.
- `volatile_attack`: high-volatility alt lane with small budget, wide stop, large target, conditional entries.
- `funding_squeeze`: futures lane using funding, open interest, basis, and liquidation-proxy context.
- `spot_accumulation`: spot-only tactical accumulation when futures risk is too crowded.

### Capital and Risk Governor

- Daily loss stop: keep current hard stop concept at `-7%`.
- Monthly loss stop: keep current hard stop concept at `-20%`.
- New target ledger:
  - monthly target equity
  - current month realized PnL
  - required daily run-rate
  - risk budget remaining
  - lane contribution
- The target ledger may raise attention and research intensity; it must not bypass kill switch, order validity, or risk sizing.

## Task 1: Growth Target Ledger

**Files:**
- Create: `src/tradecraft/services/crypto_growth_target.py`
- Test: `tests/test_crypto_growth_target.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/web/static/app.js`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from tradecraft.services.crypto_growth_target import CryptoGrowthTargetLedger


def test_monthly_target_ledger_computes_run_rate_and_gap() -> None:
    ledger = CryptoGrowthTargetLedger(monthly_target_pct=50.0)

    result = ledger.snapshot(
        start_equity_usdt=1000.0,
        current_equity_usdt=1100.0,
        elapsed_days=10.0,
        month_days=30.0,
    )

    assert result["target_equity_usdt"] == 1500.0
    assert result["current_return_pct"] == 10.0
    assert result["remaining_return_pct"] == 36.3636
    assert result["required_daily_return_pct"] > 1.0
    assert result["status"] == "behind_target"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_crypto_growth_target.py::test_monthly_target_ledger_computes_run_rate_and_gap -q
```

Expected: import failure for `tradecraft.services.crypto_growth_target`.

- [ ] **Step 3: Implement the ledger**

```python
from __future__ import annotations

from dataclasses import dataclass
from math import pow


def _pct_change(start: float, current: float) -> float:
    if start <= 0:
        return 0.0
    return (current / start - 1.0) * 100.0


@dataclass(frozen=True)
class CryptoGrowthTargetLedger:
    monthly_target_pct: float = 50.0

    def snapshot(
        self,
        *,
        start_equity_usdt: float,
        current_equity_usdt: float,
        elapsed_days: float,
        month_days: float = 30.0,
    ) -> dict[str, float | str]:
        start = max(float(start_equity_usdt), 0.0)
        current = max(float(current_equity_usdt), 0.0)
        elapsed = min(max(float(elapsed_days), 0.0), float(month_days))
        remaining_days = max(float(month_days) - elapsed, 0.0)
        target_equity = start * (1.0 + float(self.monthly_target_pct) / 100.0)
        current_return = _pct_change(start, current)
        remaining_return = _pct_change(current, target_equity)
        required_daily = 0.0
        if current > 0 and target_equity > current and remaining_days > 0:
            required_daily = (pow(target_equity / current, 1.0 / remaining_days) - 1.0) * 100.0
        if current >= target_equity:
            status = "ahead_target"
        elif current_return >= self.monthly_target_pct * (elapsed / max(float(month_days), 1.0)):
            status = "on_track"
        else:
            status = "behind_target"
        return {
            "monthly_target_pct": round(float(self.monthly_target_pct), 4),
            "start_equity_usdt": round(start, 4),
            "current_equity_usdt": round(current, 4),
            "target_equity_usdt": round(target_equity, 4),
            "current_return_pct": round(current_return, 4),
            "remaining_return_pct": round(max(remaining_return, 0.0), 4),
            "elapsed_days": round(elapsed, 4),
            "remaining_days": round(remaining_days, 4),
            "required_daily_return_pct": round(required_daily, 4),
            "status": status,
        }
```

- [ ] **Step 4: Integrate ledger into Binance snapshot**

Add a small adapter method in `BinanceBlockTrader.status()` or `snapshot()` that reads the current Binance account equity and returns:

```python
"growth_target": {
    "monthly_target_pct": 50.0,
    "target_equity_usdt": ...,
    "current_return_pct": ...,
    "required_daily_return_pct": ...,
    "status": "behind_target|on_track|ahead_target",
}
```

If no month-start equity is stored, use the first equity value seen this month and persist it in `.runtime/binance_blocks.db` in a `growth_target_snapshots` table.

- [ ] **Step 5: Add UI panel**

In `src/tradecraft/web/static/app.js`, add a panel to `renderBinanceTraderTab()`:

```javascript
function renderBinanceGrowthTarget(payload) {
  const target = payload.growth_target || {};
  return `
    <section class="memory-section binance-growth-target">
      <div class="panel-head compact">
        <h3>월간 성장 타겟</h3>
        <p>쥬가 현재 월간 50% 목표 대비 필요한 일일 속도를 계산합니다.</p>
      </div>
      <div class="binance-edge-grid">
        <div><span>목표 수익률</span><strong>${escapeHTML(fmtPercent(target.monthly_target_pct || 0, 1))}</strong></div>
        <div><span>현재 수익률</span><strong>${escapeHTML(fmtPercent(target.current_return_pct || 0, 2))}</strong></div>
        <div><span>필요 일일 속도</span><strong>${escapeHTML(fmtPercent(target.required_daily_return_pct || 0, 2))}</strong></div>
        <div><span>상태</span><strong>${escapeHTML(target.status || "-")}</strong></div>
      </div>
    </section>
  `;
}
```

- [ ] **Step 6: Verify**

Run:

```bash
python3 -m pytest tests/test_crypto_growth_target.py tests/test_binance_block_trader.py -q
node --check src/tradecraft/web/static/app.js
```

Expected: pass.

## Task 2: Lane Budget Governor

**Files:**
- Modify: `src/tradecraft/services/binance_risk.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_binance_risk.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing test**

```python
from tradecraft.services.binance_risk import BinanceRiskConfig, BinanceRiskSizer


def test_volatile_attack_uses_smaller_lane_risk_budget() -> None:
    sizer = BinanceRiskSizer(
        BinanceRiskConfig(
            account_risk_pct=0.25,
            max_symbol_exposure_pct=25.0,
            min_reward_risk=1.3,
        )
    )

    normal = sizer.size_order(
        equity_usdt=1000,
        cash_usdt=1000,
        symbol_exposure_usdt=0,
        entry_price=1.0,
        stop_price=0.95,
        target_price=1.10,
        side="long",
        quote_budget_usdt=100,
        lane="core_trend",
    )
    volatile = sizer.size_order(
        equity_usdt=1000,
        cash_usdt=1000,
        symbol_exposure_usdt=0,
        entry_price=1.0,
        stop_price=0.90,
        target_price=1.20,
        side="long",
        quote_budget_usdt=100,
        lane="volatile_attack",
    )

    assert volatile["notional_usdt"] < normal["notional_usdt"]
    assert volatile["lane"] == "volatile_attack"
```

- [ ] **Step 2: Implement lane-aware sizing**

Extend `BinanceRiskSizer.size_order(...)` with a `lane` parameter and lane multipliers:

```python
LANE_RISK_MULTIPLIERS = {
    "core_trend": 1.0,
    "spot_accumulation": 0.8,
    "intraday_reversal": 0.7,
    "funding_squeeze": 0.7,
    "volatile_attack": 0.35,
}
```

Apply the multiplier to the risk budget before quantity calculation. Keep exchange min-notional checks after the multiplier so tiny blocks do not create invalid orders.

- [ ] **Step 3: Wire lane from candidate metadata**

In `BinanceBlockTrader._normalize_block_payload(...)`, keep `metadata["lane"]`. In the sizing call, pass:

```python
lane=str(metadata.get("lane") or payload.get("lane") or "core_trend")
```

- [ ] **Step 4: Verify**

Run:

```bash
python3 -m pytest tests/test_binance_risk.py tests/test_binance_block_trader.py -q
```

Expected: pass.

## Task 3: Alpha Score v3 Candidate Fusion

**Files:**
- Create: `src/tradecraft/services/crypto_alpha_score.py`
- Test: `tests/test_crypto_alpha_score.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/services/crypto_market_research.py`

- [ ] **Step 1: Write failing test**

```python
from tradecraft.services.crypto_alpha_score import score_crypto_candidate


def test_alpha_score_rewards_volume_oi_squeeze_and_penalizes_bad_book() -> None:
    score = score_crypto_candidate(
        {
            "change_pct_24h": 14.0,
            "volume_expansion_ratio": 3.0,
            "spread_bps": 18.0,
            "orderbook_depth_usdt": 120000.0,
            "wick_risk_score": 35.0,
            "funding_rate": -0.0002,
            "open_interest": 50_000_000,
            "squeeze_risk_score": 72.0,
            "alpha_event_score": 70.0,
        }
    )

    assert score["total_score"] >= 75
    assert "volume_expansion" in score["drivers"]
    assert "squeeze_setup" in score["drivers"]
    assert not score["reject"]


def test_alpha_score_rejects_thin_wide_books() -> None:
    score = score_crypto_candidate(
        {
            "change_pct_24h": 25.0,
            "volume_expansion_ratio": 4.0,
            "spread_bps": 95.0,
            "orderbook_depth_usdt": 4000.0,
            "wick_risk_score": 80.0,
        }
    )

    assert score["reject"] is True
    assert "spread_too_wide" in score["risks"]
    assert "depth_too_thin" in score["risks"]
```

- [ ] **Step 2: Implement score object**

Return:

```python
{
    "version": "crypto_alpha_score_v3",
    "total_score": float,
    "directional_bias": "long|short|neutral",
    "drivers": list[str],
    "risks": list[str],
    "reject": bool,
}
```

The score must include:

- volume expansion
- 24h change magnitude
- spread cost
- orderbook depth
- wick risk
- funding dislocation
- open interest
- squeeze score
- alpha event score
- qualified pattern prior score
- live authority multiplier

- [ ] **Step 3: Integrate into manager candidate packets**

In `BinanceBlockTrader._manager_candidate_packets(...)`, add `alpha_score_v3` to each packet row. The manager prompt must show the score but must still use the deterministic entry gate before block creation.

- [ ] **Step 4: Verify**

Run:

```bash
python3 -m pytest tests/test_crypto_alpha_score.py tests/test_binance_block_trader.py -q
```

Expected: pass.

## Task 4: Walk-Forward Optimizer and Anti-Overfit Gate

**Files:**
- Modify: `src/tradecraft/services/crypto_pattern_lab.py`
- Test: `tests/test_crypto_pattern_lab.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing test**

```python
def test_optimized_set_requires_out_of_sample_expectancy(tmp_path):
    # Build a synthetic result where in-sample works but out-of-sample fails.
    # The service must mark it unqualified.
    result = {
        "pattern_id": "breakout_v1",
        "in_sample": {"trade_count": 40, "expectancy_r": 0.35, "profit_factor": 1.6},
        "out_of_sample": {"trade_count": 12, "expectancy_r": -0.05, "profit_factor": 0.92},
    }

    qualified = service._qualify_optimized_set(result)

    assert qualified["passed"] is False
    assert "out_of_sample_expectancy_negative" in qualified["reasons"]
```

- [ ] **Step 2: Add walk-forward fields**

Every optimized strategy set must store:

- `train_start`
- `train_end`
- `test_start`
- `test_end`
- `in_sample_expectancy_r`
- `out_of_sample_expectancy_r`
- `out_of_sample_profit_factor`
- `out_of_sample_max_drawdown_r`
- `overfit_risk`

- [ ] **Step 3: Add manager rule**

In `crypto_pattern_optimization_policy`, allow optimized sets to raise conviction only when:

- out-of-sample trade count >= 8
- out-of-sample expectancy > 0
- out-of-sample profit factor >= 1.05
- max drawdown within lane limit

- [ ] **Step 4: Verify**

Run:

```bash
python3 -m pytest tests/test_crypto_pattern_lab.py tests/test_binance_block_trader.py -q
```

Expected: pass.

## Task 5: Intraday Momentum/Reversal Router

**Files:**
- Modify: `src/tradecraft/services/crypto_quant.py`
- Test: `tests/test_crypto_quant.py`
- Modify: `src/tradecraft/services/crypto_market_research.py`
- Test: `tests/test_crypto_market_research.py`

- [ ] **Step 1: Write failing test**

```python
def test_jump_context_routes_failed_breakout_to_reversal() -> None:
    signal = engine.build_signal(
        symbol="ALTUSDT",
        horizon="intraday",
        klines_by_interval=jump_and_reversal_fixture(),
        market_features={
            "change_pct_24h": 18.0,
            "wick_risk_score": 78.0,
            "spread_bps": 12.0,
            "volume_expansion_ratio": 2.5,
        },
    )

    assert signal["regime"] == "jump_reversal"
    assert signal["short_score"] > signal["long_score"]
    assert signal["expected_r_short"] > 0
```

- [ ] **Step 2: Add router states**

Add states:

- `trend_continuation`
- `pullback_reclaim`
- `failed_breakout`
- `jump_reversal`
- `liquidity_chop`
- `squeeze_extension`

- [ ] **Step 3: Feed router state into candidate packets**

`crypto_market_research.latest_context()` must include `router_state` in each feature item and packet row.

- [ ] **Step 4: Verify**

Run:

```bash
python3 -m pytest tests/test_crypto_quant.py tests/test_crypto_market_research.py -q
```

Expected: pass.

## Task 6: Live Edge Promotion and Demotion

**Files:**
- Modify: `src/tradecraft/services/live_authority.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Test: `tests/test_live_authority.py`
- Test: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing test**

```python
def test_lane_requires_live_edge_before_budget_scale_up() -> None:
    authority = build_live_authority(
        venue="binance",
        lane="volatile_attack",
        sample_count=9,
        win_rate_pct=44.0,
        expectancy_r=-0.1,
        max_drawdown_pct=6.0,
    )

    assert authority["allow_scale_up"] is False
    assert authority["live_grade"] == "restricted"
    assert authority["max_budget_multiplier"] <= 0.5
```

- [ ] **Step 2: Add lane-level authority**

Live authority must return:

```python
{
    "venue": "binance",
    "lane_authority": {
        "volatile_attack": {
            "sample_count": 9,
            "expectancy_r": -0.1,
            "live_grade": "restricted",
            "max_budget_multiplier": 0.35,
        }
    }
}
```

- [ ] **Step 3: Enforce in candidate price plan**

If lane authority is `restricted`, force:

- waiting entry
- no immediate market-like entries
- budget multiplier <= lane authority

- [ ] **Step 4: Verify**

Run:

```bash
python3 -m pytest tests/test_live_authority.py tests/test_binance_block_trader.py -q
```

Expected: pass.

## Task 7: Binance API Rate and Cost Ledger

**Files:**
- Modify: `src/tradecraft/services/binance.py`
- Create: `src/tradecraft/services/binance_rate_ledger.py`
- Test: `tests/test_binance_adapter.py`
- Test: `tests/test_binance_rate_ledger.py`

- [ ] **Step 1: Write failing test**

```python
from tradecraft.services.binance_rate_ledger import BinanceRateLedger


def test_rate_ledger_tracks_weight_and_order_headers() -> None:
    ledger = BinanceRateLedger()
    ledger.record_response_headers(
        {
            "x-mbx-used-weight-1m": "240",
            "x-mbx-order-count-10s": "2",
            "x-mbx-order-count-1m": "8",
        }
    )

    assert ledger.snapshot()["used_weight_1m"] == 240
    assert ledger.snapshot()["order_count_10s"] == 2
    assert ledger.snapshot()["order_count_1m"] == 8
```

- [ ] **Step 2: Implement ledger**

Record Binance response headers after every REST call. Store latest counters in memory and expose them in `/api/kis/blocks/status` equivalent Binance status payload or the Binance trader status endpoint.

- [ ] **Step 3: Use bulk endpoints first**

Universe observation must use `/ticker/24hr` list endpoints. Deep research may use per-symbol endpoints, but only for `research_universe`.

- [ ] **Step 4: Verify**

Run:

```bash
python3 -m pytest tests/test_binance_adapter.py tests/test_binance_rate_ledger.py -q
```

Expected: pass.

## Task 8: Telegram and UI Reporting

**Files:**
- Modify: `src/tradecraft/services/telegram_cli.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `src/tradecraft/web/static/app.js`
- Test: `tests/test_telegram_cli.py`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: Write failing test**

```python
def test_binance_report_includes_growth_target_and_lane_pnl() -> None:
    text = render_binance_daily_report(
        {
            "growth_target": {"status": "behind_target", "required_daily_return_pct": 1.42},
            "performance_today": {"realized_pnl_usdt": 2.35, "win_rate_pct": 55.0},
            "lane_allocation": {"items": [{"lane": "volatile_attack", "value_usdt": 12.0}]},
        }
    )

    assert "월간 성장 타겟" in text
    assert "필요 일일 속도" in text
    assert "volatile_attack" in text
```

- [ ] **Step 2: Add report sections**

Reports at 06:00, 12:00, and 20:00 KST must include:

- target status
- realized PnL
- open block count
- lane PnL
- top rejected reason
- next trigger candidates
- live authority grade

- [ ] **Step 3: Verify**

Run:

```bash
python3 -m pytest tests/test_telegram_cli.py tests/test_static_ui.py -q
node --check src/tradecraft/web/static/app.js
```

Expected: pass.

## Task 9: Completion Gates for 50pct Engine

The engine is not ready for higher aggression until all gates pass:

- [ ] Backtest gate: at least 3 months of walk-forward tests for each lane.
- [ ] Live paper/live-small gate: at least 30 closed blocks or 7 days per lane.
- [ ] Expectancy gate: positive live expectancy after fees/slippage.
- [ ] Drawdown gate: no lane exceeds its max drawdown threshold.
- [ ] Rate gate: Binance rate ledger stays below 50% of documented weight/order limits during normal operation.
- [ ] Prompt gate: manager prompt stays below 190k chars with 30-60 candidates.
- [ ] UI gate: Binance tab shows target ledger, universe funnel, lane PnL, live authority, and rejected reasons.

## Verification Commands

Run after implementing the full plan:

```bash
python3 -m pytest \
  tests/test_crypto_growth_target.py \
  tests/test_crypto_alpha_score.py \
  tests/test_crypto_quant.py \
  tests/test_crypto_market_research.py \
  tests/test_crypto_pattern_lab.py \
  tests/test_binance_risk.py \
  tests/test_binance_adapter.py \
  tests/test_binance_block_trader.py \
  tests/test_binance_block_trader_runner.py \
  tests/test_live_authority.py \
  tests/test_static_ui.py \
  tests/test_telegram_cli.py -q

python3 -m ruff check src tests
node --check src/tradecraft/web/static/app.js
git diff --check
```

## Rollout Plan

1. Keep live trading enabled only at current small size until the target ledger and lane scorecards exist.
2. Run the new target ledger for 7 days without changing order size.
3. Enable lane budget governor with `volatile_attack` still capped at 0.35x.
4. Run walk-forward optimizer nightly and allow only out-of-sample-qualified sets into manager prompt.
5. After 30 closed Binance blocks, let live authority decide whether any lane can scale up.
6. Review monthly target progress every day at 20:00 KST.
7. If a lane creates negative expectancy after 10 samples, demote it to observe-only until the weekly review creates a revised rule.

## Self-Review

- No placeholder tasks remain.
- Each subsystem has a concrete file path and test path.
- The plan does not bypass existing HERMES safety gates.
- The monthly 50% target is represented as a measurable ledger and governor input, not as a blind order instruction.
- The plan keeps 300-symbol observation separate from 80-symbol deep research and 30-60-symbol manager prompts.
