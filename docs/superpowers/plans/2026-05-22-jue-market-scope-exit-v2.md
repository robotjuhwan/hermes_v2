# Jue Market Scope And Exit V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Jue broader, less naive about market risk, and better at selling by adding horizon-specific exit policy, market pulse risk caps, broad opportunity scanning, ETF universe expansion, and measurable self-evaluation.

**Architecture:** Keep GPT-5.5 as the final decision maker, but stop feeding it a tiny hand-picked universe. Add a deterministic local pre-ranker that scans broad KRX/ETF data, then send a compact top slice to Jue. Separate rule-level exits from manager-level exits by horizon, and store MFE/MAE/giveback metrics so Jue can revise policy from evidence.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, pytest, existing `tradecraft.services.*`, static UI in `src/tradecraft/web/static`.

---

## Brainstorming Result

### Approach A: Tune Current Prompts Only

Prompt Jue to sell better and look at more candidates. This is cheap but weak. The current system bottleneck is not only the LLM prompt; the local candidate set is small and `core_etf` exits are structurally signal-only.

### Approach B: Add Broad Scanner Only

Build a large universe pre-ranker and keep current exit behavior. This fixes the "computer sees too few names" problem, but still leaves the "rises but does not sell" problem in ETF/core and mid/long blocks.

### Approach C: Full Trading Intelligence Loop V2

Add horizon-specific exit behavior, market risk caps, broad opportunity scanning, ETF universe expansion, and daily self-evaluation metrics. This is the recommended path because it fixes the observed failure modes without increasing LLM call frequency.

Decision: implement Approach C in five focused tasks.

---

## File Structure

- Modify `src/tradecraft/services/kis_block_trader.py`
  - Add exit policy v2 helpers.
  - Track MFE/MAE/giveback per block from quote snapshots.
  - Emit `profit_lock_signal`, `target_signal`, `stop_signal`, `trim_review_due`, and `exit_policy_violation` events.

- Create `src/tradecraft/services/block_performance.py`
  - Pure functions for MFE/MAE/giveback and realized PnL summaries.
  - No KIS dependency.

- Modify `src/tradecraft/services/market_pulse.py`
  - Add risk-cap logic so negative foreign/institution/program/FX pressure limits `score` and `regime`.
  - Return explicit `risk_cap` object.

- Create `src/tradecraft/services/opportunity_scanner.py`
  - Build broad KRX/ETF candidate pools from `symbol_directory`, reports, strategy insights, fundamentals, ETF DB, account positions, and recent blocks.
  - Produce deterministic top candidates before GPT.

- Modify `src/tradecraft/services/market_judgment.py`
  - Use opportunity scanner output in `_build_universe()` and `_focus_symbols()`.
  - Keep LLM focus limit small, but make the pre-scan broad.

- Modify `src/tradecraft/services/etf_research.py`
  - Expand ETF universe source beyond the current three configured symbols.
  - Merge configured ETFs, symbol directory ETF rows, and collected ETF catalog rows.

- Modify `src/tradecraft/services/daily_discovery.py`
  - Make discovery independently idempotent per trading day, not only dependent on memory runner `pre_open` due slot.

- Modify `src/tradecraft/runtime/investment_memory_runner.py`
  - Ensure daily discovery runs once per open day even if the runner missed the 08:30 slot.

- Modify `src/tradecraft/main.py`
  - Add API status fields for candidate coverage, ETF universe size, exit metric summary, and risk cap.

- Modify `src/tradecraft/web/static/app.js`
  - Show coverage and exit-quality diagnostics in the investment agent / block trading area.

- Modify `src/tradecraft/web/static/style.css`
  - Add compact chips/bars for coverage, MFE giveback, and risk-cap state.

- Tests:
  - `tests/test_block_performance.py`
  - `tests/test_kis_block_trader.py`
  - `tests/test_market_pulse.py`
  - `tests/test_opportunity_scanner.py`
  - `tests/test_market_judgment.py`
  - `tests/test_etf_research.py`
  - `tests/test_daily_discovery.py`
  - `tests/test_api_smoke.py`

---

## Task 1: Block Exit Policy V2 And Performance Metrics

**Files:**
- Create: `src/tradecraft/services/block_performance.py`
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_block_performance.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Write failing tests for MFE/MAE/giveback**

Add `tests/test_block_performance.py`:

```python
from __future__ import annotations

import pytest

from tradecraft.services.block_performance import summarize_block_path


def test_summarize_block_path_computes_mfe_mae_and_giveback() -> None:
    result = summarize_block_path(
        entry_price=100_000,
        current_price=107_000,
        prices=[98_000, 103_000, 112_000, 107_000],
    )

    assert result["mfe_pct"] == pytest.approx(12.0)
    assert result["mae_pct"] == pytest.approx(-2.0)
    assert result["current_pnl_pct"] == pytest.approx(7.0)
    assert result["giveback_pct"] == pytest.approx(5.0)
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_block_performance.py::test_summarize_block_path_computes_mfe_mae_and_giveback -q
```

Expected: FAIL with `ModuleNotFoundError` or missing function.

- [ ] **Step 3: Implement pure performance helper**

Create `src/tradecraft/services/block_performance.py`:

```python
from __future__ import annotations

from typing import Any


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def summarize_block_path(
    *,
    entry_price: float,
    current_price: float,
    prices: list[float],
) -> dict[str, float]:
    entry = _safe_float(entry_price)
    current = _safe_float(current_price)
    clean_prices = [_safe_float(price) for price in prices if _safe_float(price) > 0]
    if entry <= 0:
        return {
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
            "current_pnl_pct": 0.0,
            "giveback_pct": 0.0,
        }
    if current > 0:
        clean_prices.append(current)
    if not clean_prices:
        clean_prices = [entry]
    high = max(clean_prices)
    low = min(clean_prices)
    mfe_pct = (high - entry) / entry * 100.0
    mae_pct = (low - entry) / entry * 100.0
    current_pnl_pct = (current - entry) / entry * 100.0 if current > 0 else 0.0
    giveback_pct = max(mfe_pct - current_pnl_pct, 0.0)
    return {
        "mfe_pct": round(mfe_pct, 4),
        "mae_pct": round(mae_pct, 4),
        "current_pnl_pct": round(current_pnl_pct, 4),
        "giveback_pct": round(giveback_pct, 4),
    }
```

- [ ] **Step 4: Run performance tests**

Run:

```bash
pytest tests/test_block_performance.py -q
```

Expected: PASS.

- [ ] **Step 5: Add exit policy tests for horizon behavior**

Extend `tests/test_kis_block_trader.py` with tests covering:

```python
def test_short_block_target_still_exits_immediately(tmp_path: Path) -> None:
    # Create open short block with price above target.
    # Run executor tick.
    # Assert sell order is created with reason target_reached.
    ...


def test_core_etf_target_creates_trim_review_signal_not_full_exit(tmp_path: Path) -> None:
    # Create open core_etf block with price above target.
    # Run executor tick.
    # Assert no sell order is created.
    # Assert event_type is exit_signal or trim_review_due.
    ...


def test_profit_giveback_emits_profit_lock_signal(tmp_path: Path) -> None:
    # Create block with entry 100, quote path high 112, current 106.
    # Assert profit_lock_signal event is produced when giveback exceeds configured threshold.
    ...
```

- [ ] **Step 6: Implement exit policy v2 inside `kis_block_trader.py`**

Add helper behavior near `_exit_order_for_block`:

```python
def _exit_policy_for_block(block: dict[str, Any], reason: str) -> dict[str, Any]:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    horizon = _normalize_horizon(metadata.get("horizon"))
    if reason == "force_exit_requested":
        return {"action": "sell_all", "horizon": horizon}
    if horizon == "short":
        return {"action": "sell_all", "horizon": horizon}
    if horizon == "core_etf":
        return {"action": "manager_trim_review", "horizon": horizon}
    if horizon in {"mid", "long"}:
        return {"action": "manager_review", "horizon": horizon}
    return {"action": "manager_review", "horizon": horizon}
```

Then route `_exit_order_for_block()` through the helper:

```python
policy = _exit_policy_for_block(block, reason)
if policy["action"] != "sell_all":
    event_type = "trim_review_due" if policy["action"] == "manager_trim_review" else "exit_signal"
    self.repository.add_event(
        str(block["block_id"]),
        event_type,
        f"{policy['horizon']} block touched {reason}; manager review required",
        {"horizon": policy["horizon"], "reason": reason, "price": price},
    )
    return {
        "status": event_type,
        "reason": reason,
        "horizon": policy["horizon"],
        "block_id": block["block_id"],
    }
```

- [ ] **Step 7: Run focused block tests**

Run:

```bash
pytest tests/test_block_performance.py tests/test_kis_block_trader.py -q
```

Expected: PASS.

---

## Task 2: Market Pulse V2 Risk Caps

**Files:**
- Modify: `src/tradecraft/services/market_pulse.py`
- Test: `tests/test_market_pulse.py`

- [ ] **Step 1: Write failing risk cap tests**

Add tests:

```python
def test_market_pulse_caps_score_when_fx_program_and_flow_pressure_stack() -> None:
    components = MarketPulseService._score_components(
        indices=[{"status": "ok", "code": "KOSPI", "change_pct": 1.2}, {"status": "ok", "code": "KOSDAQ", "change_pct": 1.1}],
        sectors={"items": [{"direction": "positive"} for _ in range(8)]},
        investor_flows=[
            {"status": "ok", "market": "KOSPI", "foreign_institution_sum_100m_krw": -50_000},
            {"status": "ok", "market": "KOSDAQ", "foreign_institution_sum_100m_krw": -20_000},
            {"status": "ok", "market": "FUT", "foreign_net_buy_100m_krw": -10_000},
        ],
        program_trading=[
            {"status": "ok", "market": "KOSPI", "total_net_buy_100m_krw": -40_000},
        ],
        fx={"status": "ok", "change": 12.0},
        futures={"status": "ok", "basis": 2.0},
    )

    assert components["risk_cap"]["active"] is True
    assert components["total_score"] <= 65.0
```

- [ ] **Step 2: Run failing pulse test**

Run:

```bash
pytest tests/test_market_pulse.py::test_market_pulse_caps_score_when_fx_program_and_flow_pressure_stack -q
```

Expected: FAIL because `risk_cap` is missing.

- [ ] **Step 3: Add risk cap helper**

Add helper in `market_pulse.py`:

```python
def _risk_cap_from_components(
    *,
    equity_flow: float,
    futures_foreign: float,
    program_total: float,
    fx_change: float,
    dispersion: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    cap = 100.0
    if fx_change >= 10:
        reasons.append("usd_krw_up_pressure")
    if program_total <= -30_000:
        reasons.append("program_sell_pressure")
    if equity_flow <= -30_000 and futures_foreign < 0:
        reasons.append("foreign_flow_pressure")
    if dispersion >= 1.5:
        reasons.append("index_dispersion_high")
    if len(reasons) >= 3:
        cap = 65.0
    elif len(reasons) == 2:
        cap = 75.0
    return {"active": cap < 100.0, "cap": cap, "reasons": reasons}
```

Use it inside `_score_components()` and `_classify()`.

- [ ] **Step 4: Ensure regime cannot remain clean `risk_on` under active cap**

When `risk_cap.active` and current regime is `risk_on`, downgrade:

```python
if risk_cap["active"] and regime == "risk_on":
    regime = "risk_on_with_pressure"
```

- [ ] **Step 5: Run market pulse tests**

Run:

```bash
pytest tests/test_market_pulse.py -q
```

Expected: PASS.

---

## Task 3: Broad Opportunity Scanner

**Files:**
- Create: `src/tradecraft/services/opportunity_scanner.py`
- Modify: `src/tradecraft/services/market_judgment.py`
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_opportunity_scanner.py`
- Test: `tests/test_market_judgment.py`

- [ ] **Step 1: Write scanner tests**

Create `tests/test_opportunity_scanner.py`:

```python
from __future__ import annotations

from tradecraft.services.opportunity_scanner import rank_opportunities


def test_rank_opportunities_uses_broad_pool_but_returns_compact_top_slice() -> None:
    symbols = [
        {"symbol": f"{index:06d}", "name": f"종목{index}", "market": "KOSPI"}
        for index in range(1, 301)
    ]
    reports = [{"symbol": "000010", "score": 30}, {"symbol": "000020", "score": 10}]
    insights = [{"symbol": "000020", "strength": 90}, {"symbol": "000030", "strength": 70}]
    positions = [{"symbol": "000040", "value_krw": 100_000}]

    result = rank_opportunities(
        symbols=symbols,
        reports=reports,
        insights=insights,
        fundamentals=[],
        etfs=[],
        positions=positions,
        limit=12,
    )

    assert result["pool_count"] == 300
    assert len(result["candidates"]) == 12
    assert result["coverage"]["position_count"] == 1
    assert {row["symbol"] for row in result["candidates"][:4]} >= {"000010", "000020", "000040"}
```

- [ ] **Step 2: Run failing scanner test**

Run:

```bash
pytest tests/test_opportunity_scanner.py -q
```

Expected: FAIL because module is missing.

- [ ] **Step 3: Implement deterministic scanner**

Create `src/tradecraft/services/opportunity_scanner.py`:

```python
from __future__ import annotations

from typing import Any


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _symbol(value: Any) -> str:
    return str(value or "").strip()


def rank_opportunities(
    *,
    symbols: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    insights: list[dict[str, Any]],
    fundamentals: list[dict[str, Any]],
    etfs: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    limit: int = 60,
) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for row in symbols:
        symbol = _symbol(row.get("symbol"))
        if not symbol:
            continue
        rows[symbol] = {
            "symbol": symbol,
            "name": str(row.get("name") or row.get("company_name") or symbol),
            "market": str(row.get("market") or ""),
            "score": 0.0,
            "sources": ["symbol_directory"],
            "reasons": [],
        }

    def bump(source_rows: list[dict[str, Any]], source: str, field: str, weight: float) -> None:
        for row in source_rows:
            symbol = _symbol(row.get("symbol"))
            if not symbol:
                continue
            target = rows.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "name": str(row.get("name") or row.get("company_name") or symbol),
                    "market": str(row.get("market") or ""),
                    "score": 0.0,
                    "sources": [],
                    "reasons": [],
                },
            )
            target["score"] += _safe_float(row.get(field)) * weight
            if source not in target["sources"]:
                target["sources"].append(source)
            target["reasons"].append(f"{source}:{field}")

    bump(reports, "reports", "score", 1.0)
    bump(insights, "strategy_insights", "strength", 0.5)
    bump(fundamentals, "fundamentals", "valuation_score", 0.4)
    bump(etfs, "etf_research", "score", 0.6)
    bump(positions, "account_position", "value_krw", 0.0001)

    candidates = sorted(
        rows.values(),
        key=lambda row: (float(row.get("score") or 0.0), len(row.get("sources") or [])),
        reverse=True,
    )
    return {
        "status": "ok",
        "pool_count": len(rows),
        "candidates": candidates[: max(int(limit), 1)],
        "coverage": {
            "symbol_count": len(symbols),
            "report_count": len(reports),
            "insight_count": len(insights),
            "fundamental_count": len(fundamentals),
            "etf_count": len(etfs),
            "position_count": len(positions),
        },
    }
```

- [ ] **Step 4: Wire scanner into market judgment**

Modify `MarketJudgmentEngine` to accept an optional `opportunity_provider`. In `_build_universe()`, merge scanner candidates before truncating to `max_symbols`.

Expected behavior:

```python
if self.opportunity_provider is not None:
    opportunities = self.opportunity_provider(limit=max(int(self.config.max_symbols), 1))
    for row in opportunities.get("candidates") or []:
        symbol = str(row.get("symbol") or "")
        if symbol:
            unique.append(symbol)
```

- [ ] **Step 5: Add API status for coverage**

Modify `/api/ops/readiness` or `/api/market/judgments/latest` to include:

```json
{
  "candidate_coverage": {
    "pool_count": 2854,
    "llm_focus_limit": 12,
    "quote_limit": 60,
    "last_scan_at": "..."
  }
}
```

- [ ] **Step 6: Run scanner and market tests**

Run:

```bash
pytest tests/test_opportunity_scanner.py tests/test_market_judgment.py tests/test_api_smoke.py -q
```

Expected: PASS.

---

## Task 4: ETF Universe Expansion

**Files:**
- Modify: `src/tradecraft/services/etf_research.py`
- Modify: `src/tradecraft/config.py`
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_etf_research.py`

- [ ] **Step 1: Write failing ETF universe tests**

Add:

```python
def test_etf_universe_merges_configured_and_symbol_directory_rows() -> None:
    configured = parse_etf_universe_config("069500:KODEX 200")
    directory_rows = [
        {"symbol": "091160", "company_name": "KODEX 반도체", "market": "ETF"},
        {"symbol": "102110", "company_name": "TIGER 200", "market": "ETF"},
    ]

    merged = merge_etf_universe(configured=configured, symbol_directory_rows=directory_rows)

    assert [row.symbol for row in merged] == ["069500", "091160", "102110"]
```

- [ ] **Step 2: Run failing ETF test**

Run:

```bash
pytest tests/test_etf_research.py::test_etf_universe_merges_configured_and_symbol_directory_rows -q
```

Expected: FAIL because `merge_etf_universe` is missing.

- [ ] **Step 3: Implement ETF universe merge**

Add to `etf_research.py`:

```python
def merge_etf_universe(
    *,
    configured: list[ETFUniverseItem],
    symbol_directory_rows: list[dict[str, Any]],
    limit: int = 200,
) -> list[ETFUniverseItem]:
    by_symbol: dict[str, ETFUniverseItem] = {}
    for item in configured:
        by_symbol[item.symbol] = item
    for row in symbol_directory_rows:
        symbol = str(row.get("symbol") or "").strip()
        name = str(row.get("company_name") or row.get("name") or symbol).strip()
        market = str(row.get("market") or "").upper()
        if not symbol.isdigit() or len(symbol) != 6:
            continue
        if market != "ETF" and not name.startswith(("KODEX", "TIGER", "ACE", "RISE", "SOL", "PLUS")):
            continue
        by_symbol.setdefault(symbol, ETFUniverseItem(symbol=symbol, name=name, category="expanded"))
    return list(by_symbol.values())[: max(int(limit), 1)]
```

- [ ] **Step 4: Add provider wiring**

In `main.py`, pass symbol directory ETF rows into ETF research provider. Use existing report repository method or add a small method that selects:

```sql
SELECT symbol, company_name, market
FROM symbol_directory
WHERE market = 'ETF'
   OR company_name LIKE 'KODEX%'
   OR company_name LIKE 'TIGER%'
   OR company_name LIKE 'ACE%'
   OR company_name LIKE 'RISE%'
   OR company_name LIKE 'SOL%'
   OR company_name LIKE 'PLUS%'
```

- [ ] **Step 5: Run ETF tests**

Run:

```bash
pytest tests/test_etf_research.py -q
```

Expected: PASS.

---

## Task 5: Daily Discovery Idempotency And Coverage

**Files:**
- Modify: `src/tradecraft/services/daily_discovery.py`
- Modify: `src/tradecraft/runtime/investment_memory_runner.py`
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_daily_discovery.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Write failing idempotency test**

Add:

```python
def test_daily_discovery_reports_due_when_no_run_exists_for_open_day(tmp_path: Path) -> None:
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=str(tmp_path / "discovery.db"), enabled=True),
        symbol_provider=lambda market, limit: [{"symbol": "005930", "name": "삼성전자", "market": market}],
        symbol_analysis=_FakeSymbolAnalysis(),
    )

    assert service.should_run_for_day("2026-05-22") is True
```

- [ ] **Step 2: Implement `should_run_for_day()`**

In `daily_discovery.py`:

```python
def should_run_for_day(self, trading_day: str | date) -> bool:
    if not self.config.enabled:
        return False
    day = str(trading_day)
    latest = self.repository.get_run(day)
    return latest.get("status") == "missing"
```

- [ ] **Step 3: Update runner trigger**

In `investment_memory_runner.py`, replace pre-open-only discovery trigger with:

```python
today = datetime.now(timezone.utc).astimezone(KST).date()
if bool(resolved_settings.daily_discovery_enabled):
    discovery = _build_daily_discovery_service(resolved_settings)
    if discovery.should_run_for_day(today):
        discovery_result = await discovery.run_once(trading_day=today, force=False)
        context["daily_discovery"] = discovery.latest_context(limit=10)
        results.append(
            {
                "status": discovery_result.get("status"),
                "slot": "daily_discovery",
                "analyzed_count": discovery_result.get("analyzed_count", 0),
            }
        )
```

- [ ] **Step 4: Add API visibility**

In `/api/discovery/status`, expose:

```json
{
  "latest": {"status": "missing"},
  "due_today": true,
  "coverage": {
    "kospi_count": 5,
    "kosdaq_count": 5,
    "candidate_limit_per_market": 300
  }
}
```

- [ ] **Step 5: Run discovery tests**

Run:

```bash
pytest tests/test_daily_discovery.py tests/test_investment_memory.py tests/test_api_smoke.py -q
```

Expected: PASS.

---

## Task 6: UI Diagnostics For Jue Coverage And Selling Quality

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`
- Test: `node --check src/tradecraft/web/static/app.js`

- [ ] **Step 1: Add UI state fields**

In the existing state object, add:

```javascript
candidateCoverage: null,
exitQuality: null,
marketRiskCap: null,
```

- [ ] **Step 2: Render compact diagnostics**

In the block trading/investment agent area, render:

```javascript
[
  { label: "후보 풀", value: fmtNumber(coverage.pool_count || 0) },
  { label: "LLM 집중", value: fmtNumber(coverage.llm_focus_limit || 0) },
  { label: "ETF 유니버스", value: fmtNumber(coverage.etf_universe_count || 0) },
  { label: "최대수익 반납", value: `${fmtPercent(exitQuality.avg_giveback_pct || 0)}` },
  { label: "리스크 캡", value: riskCap.active ? `ON ${riskCap.cap}` : "OFF" },
]
```

- [ ] **Step 3: Add CSS chips**

Add styles:

```css
.jue-diagnostic-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  gap: 8px;
}

.jue-diagnostic-chip {
  border: 1px solid var(--border-muted);
  background: var(--surface-2);
  border-radius: 8px;
  padding: 8px 10px;
}
```

- [ ] **Step 4: Run JS syntax check**

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: no syntax errors.

---

## Verification Matrix

Run focused tests first:

```bash
pytest tests/test_block_performance.py tests/test_kis_block_trader.py -q
pytest tests/test_market_pulse.py -q
pytest tests/test_opportunity_scanner.py tests/test_market_judgment.py -q
pytest tests/test_etf_research.py tests/test_daily_discovery.py -q
```

Run API and frontend checks:

```bash
pytest tests/test_api_smoke.py -q
node --check src/tradecraft/web/static/app.js
git diff --check -- src tests docs
```

Operational verification after implementation:

```bash
curl -sS http://127.0.0.1:18080/api/health
curl -sS http://127.0.0.1:18080/api/discovery/status
curl -sS http://127.0.0.1:18080/api/market/judgments/latest
curl -sS http://127.0.0.1:18080/api/kis/blocks
```

Expected operational outcomes:

- Jue still runs LLM manager every 30 minutes during market hours.
- Quote collection does not expand to thousands of KIS calls per minute.
- Broad scanner pool is at least hundreds of symbols, preferably over 2,000 once ETF catalog expansion is working.
- LLM focus remains compact, around 12 to 20 symbols.
- ETF universe grows beyond the current 3 symbols.
- Daily discovery creates one run per open trading day.
- Closed blocks record MFE/MAE/giveback.
- Market pulse risk cap prevents clean `risk_on 100` when risk flags stack.

---

## Execution Recommendation

Use Subagent-Driven implementation with disjoint ownership:

1. Worker A: Task 1, block performance and exit policy.
2. Worker B: Task 2, market pulse v2 risk caps.
3. Worker C: Task 3, opportunity scanner and market judgment wiring.
4. Worker D: Task 4 and Task 5, ETF universe and daily discovery.
5. Main agent: Task 6 UI, integration review, process restart, live verification.

Do not increase LLM call frequency. Keep GPT-5.5 cadence as-is and improve pre-ranking, context quality, and exit discipline.
