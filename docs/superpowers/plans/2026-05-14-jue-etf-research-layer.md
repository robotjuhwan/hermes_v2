# Jue ETF Research Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ETF/Core a first-class asset class that Jue can research, score, allocate, trade, and review separately from individual stocks.

**Architecture:** Keep the existing KIS block execution path, because ETFs already trade through the same domestic quote/order adapter. Add a small ETF research layer beside company fundamentals, then feed ETF-specific context into strategy intelligence, the KIS block manager, UI, and memory. Do not evaluate ETFs with company PER/PBR/ROE logic.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, existing `KISAdapter`, existing `KISBlockTrader`, static frontend in `src/tradecraft/web/static`.

---

## File Structure

- Create `src/tradecraft/services/etf_research.py`: ETF universe, snapshots, scores, and repository.
- Modify `src/tradecraft/config.py`: ETF research DB path, universe env, collection limits.
- Modify `src/tradecraft/main.py`: wire ETF repository/service and add protected ETF API routes.
- Modify `src/tradecraft/services/strategy_intelligence.py`: add ETF/Core candidates without company valuation scoring.
- Modify `src/tradecraft/services/kis_block_trader.py`: inject ETF research context into Jue's manager prompt.
- Modify `src/tradecraft/services/investment_memory.py`: include ETF/Core notes in memory context and daily review.
- Modify `src/tradecraft/web/static/app.js`: show ETF/Core research and allocation state.
- Modify `src/tradecraft/web/static/style.css`: add ETF/Core board styling.
- Create `tests/test_etf_research.py`: ETF repository, scoring, collection target behavior.
- Modify `tests/test_strategy_intelligence.py`: ETF candidates are included and not penalized for missing company valuation.
- Modify `tests/test_kis_block_trader.py`: manager prompt receives ETF research context.
- Modify `tests/test_api_smoke.py` or create `tests/test_etf_research_api.py`: ETF API smoke and auth behavior.

## Task 1: ETF Research Repository

**Files:**
- Create: `src/tradecraft/services/etf_research.py`
- Test: `tests/test_etf_research.py`

- [ ] **Step 1: Write failing repository tests**

Test these exact behaviors:
- default universe accepts `069500:KODEX 200,102110:TIGER 200`
- ETF code/name/category are stored
- latest snapshot returns `status="missing"` before collection
- score labels use ETF terms: `core_fit`, `liquidity_watch`, `theme_momentum`, `unknown`

Run:

```bash
pytest tests/test_etf_research.py -q
```

Expected: fail because `tradecraft.services.etf_research` does not exist.

- [ ] **Step 2: Implement minimal repository**

Create:
- `ETFUniverseItem`
- `ETFMarketSnapshot`
- `ETFScore`
- `ETFResearchRepository`
- `parse_etf_universe_config(value: str) -> list[ETFUniverseItem]`
- `score_etf_snapshot(snapshot: ETFMarketSnapshot) -> ETFScore`

SQLite tables:
- `etf_universe(symbol primary key, name, category, tags_json, updated_at)`
- `etf_market_snapshots(symbol, price, change_pct, volume, turnover_krw, source, raw_json, captured_at, status, error_message)`
- `etf_scores(symbol primary key, label, liquidity_score, momentum_score, core_fit_score, risk_score, reasons_json, risks_json, scored_at)`

Run:

```bash
pytest tests/test_etf_research.py -q
```

Expected: pass.

## Task 2: ETF Quote Collection and API

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `src/tradecraft/main.py`
- Test: `tests/test_etf_research_api.py`

- [ ] **Step 1: Add config tests**

Add assertions for:
- `TRADECRAFT_ETF_RESEARCH_DB_PATH`
- `TRADECRAFT_ETF_RESEARCH_UNIVERSE`
- `TRADECRAFT_ETF_RESEARCH_MAX_SYMBOLS`

Run:

```bash
pytest tests/test_config.py -q
```

Expected: fail until config fields exist.

- [ ] **Step 2: Add ETF collection API**

Add protected routes:
- `GET /api/etf/research/status`
- `GET /api/etf/research/candidates`
- `POST /api/etf/research/collect`

Collection behavior:
- seed configured ETF universe first
- fetch each ETF with `KISAdapter.fetch_domestic_quote`
- store raw KIS output in snapshot
- if KIS fails, store `status="error"` with `error_message`
- no fallback buy/sell decision is produced from failed ETF data

Run:

```bash
pytest tests/test_etf_research_api.py -q
```

Expected: pass with fake KIS.

## Task 3: Strategy Intelligence Integration

**Files:**
- Modify: `src/tradecraft/services/strategy_intelligence.py`
- Test: `tests/test_strategy_intelligence.py`

- [ ] **Step 1: Write ETF candidate tests**

Add tests proving:
- ETF candidates can appear even without company reports
- ETF candidates include `asset_class="etf"`
- ETF candidates do not receive company valuation warnings like `밸류 미수집`
- ETF scoring uses liquidity/momentum/core-fit signals

Run:

```bash
pytest tests/test_strategy_intelligence.py -q
```

Expected: fail until ETF research provider is wired.

- [ ] **Step 2: Add ETF candidate source**

Add an optional ETF research repository/provider to `StrategyIntelligenceService`.

Candidate payload should include:

```python
{
    "asset_class": "etf",
    "horizon_bias": "core_etf",
    "valuation": {"status": "not_applicable", "label": "etf"},
    "data_coverage": {"has_etf_research": True},
}
```

Run:

```bash
pytest tests/test_strategy_intelligence.py -q
```

Expected: pass.

## Task 4: Jue Block Manager Prompt Upgrade

**Files:**
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Test: `tests/test_kis_block_trader.py`

- [ ] **Step 1: Write prompt test**

Assert manager prompt contains:
- `etf_research`
- `etf_policy`
- `core_etf` guidance saying target/stop are rebalance/risk thresholds, not scalp triggers

Run:

```bash
pytest tests/test_kis_block_trader.py::test_manager_prompt_requires_horizon_and_portfolio_balance -q
```

Expected: fail until prompt context is added.

- [ ] **Step 2: Inject ETF research context**

Add ETF context beside existing `etf_universe`:
- latest ETF scores
- current ETF/Core allocation
- missing ETF data warnings
- allowed ETF universe

Run:

```bash
pytest tests/test_kis_block_trader.py -q
```

Expected: pass.

## Task 5: UI ETF/Core Board

**Files:**
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`

- [ ] **Step 1: Add render tests if existing frontend test harness supports it**

If no JS test harness exists, use static checks and browser verification.

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: pass before and after edit.

- [ ] **Step 2: Add ETF/Core area**

In the block trading view, show:
- ETF/Core target vs actual allocation
- configured ETF universe
- latest ETF scores
- stale/error chips
- “ETF 리서치 갱신” button calling `POST /api/etf/research/collect`

Run:

```bash
node --check src/tradecraft/web/static/app.js
```

Expected: pass.

## Task 6: Memory and Daily Review

**Files:**
- Modify: `src/tradecraft/services/investment_memory.py`
- Test: `tests/test_investment_memory.py`

- [ ] **Step 1: Write memory context test**

Assert `context_pack()` includes:
- ETF/Core allocation
- active ETF blocks
- ETF research stale/error state
- ETF policy notes

Run:

```bash
pytest tests/test_investment_memory.py -q
```

Expected: fail until ETF memory context is added.

- [ ] **Step 2: Add ETF memory context**

Daily journals should mention ETF/Core only when relevant:
- allocation drift
- ETF block opened/closed
- ETF research stale/error
- ETF exposure replacing or complementing single-stock risk

Run:

```bash
pytest tests/test_investment_memory.py -q
```

Expected: pass.

## Task 7: Final Verification

- [ ] **Step 1: Run focused tests**

```bash
pytest tests/test_etf_research.py tests/test_etf_research_api.py tests/test_strategy_intelligence.py tests/test_kis_block_trader.py tests/test_investment_memory.py -q
```

Expected: pass.

- [ ] **Step 2: Run smoke checks**

```bash
pytest tests/test_api_smoke.py -q
node --check src/tradecraft/web/static/app.js
git diff --check
```

Expected: pass with no whitespace errors.

- [ ] **Step 3: Runtime smoke**

With admin token set, check:

```bash
curl -s http://127.0.0.1:18080/api/etf/research/status
curl -s -X POST http://127.0.0.1:18080/api/etf/research/collect
curl -s http://127.0.0.1:18080/api/kis/blocks/status
```

Expected:
- ETF research status returns configured universe
- collect stores snapshots or explicit errors
- KIS block status still returns normally

## Self-Review

- Spec coverage: ETF trading already exists; this plan adds ETF research, strategy integration, manager context, UI, memory, and tests.
- Placeholder scan: no `TBD`, no “implement later”, no unspecified edge handling.
- Type consistency: uses ETF-specific repository/provider concepts and keeps company fundamentals separate.
