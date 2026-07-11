# KIS ETF Daily Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small ETF-specific daily discovery lane so KIS Jue analyzes configured ETF symbols and feeds the existing investment memory and Jue Wiki rebuild path.

**Architecture:** Extend the existing `DailyDiscoveryService` rather than adding a second runner. ETF rows use the same symbol-analysis persistence path, but carry `asset_class="etf"` and trigger `daily_etf_deep_research`; Jue Wiki already consumes daily discovery rows when rebuilding KIS symbol pages.

**Tech Stack:** Python 3.10+, pytest, SQLite-backed repositories, existing static runtime wiring.

## Global Constraints

- Preserve existing KOSPI/KOSDAQ daily discovery behavior.
- Do not introduce new dependencies.
- ETF discovery must be configurable via settings and default to a small count.
- ETF rows must remain distinguishable by `market="ETF"` and `asset_class="etf"`.
- Existing Jue Wiki rebuild should consume ETF discovery through the existing daily discovery DB path.
- Do not commit unless explicitly requested.

---

### Task 1: ETF Symbol Selection

**Files:**
- Modify: `src/tradecraft/services/daily_discovery.py`
- Modify: `src/tradecraft/services/naver_reports.py`
- Test: `tests/test_daily_discovery.py`

**Interfaces:**
- Produces: `DailyDiscoveryConfig.etf_count: int`
- Produces: `DailyDiscoveryService.select_symbols(...)` returns ETF rows when `etf_count > 0`.
- Produces: `NaverReportRepository.list_symbol_directory(..., asset_class="etf")`.

- [ ] Add failing tests proving ETF rows can be listed and selected separately from stocks.
- [ ] Implement `asset_class` filtering in the symbol directory.
- [ ] Implement ETF selection in daily discovery without changing KOSPI/KOSDAQ counts.
- [ ] Run `pytest tests/test_daily_discovery.py -q`.

### Task 2: ETF Analysis Trigger And Summary

**Files:**
- Modify: `src/tradecraft/services/daily_discovery.py`
- Modify: `src/tradecraft/services/symbol_analysis.py`
- Test: `tests/test_daily_discovery.py`
- Test: `tests/test_symbol_analysis.py`

**Interfaces:**
- Produces: `ETF_DISCOVERY_TRIGGER = "daily_etf_deep_research"`.
- Produces: ETF rows call symbol analysis with the ETF trigger.
- Produces: symbol analysis prompt contains ETF-specific guidance when trigger is ETF discovery.

- [ ] Add failing tests for ETF trigger and prompt guidance.
- [ ] Implement trigger selection and compact ETF metadata preservation.
- [ ] Add prompt guidance for ETF analysis.
- [ ] Run focused tests.

### Task 3: Runtime Wiring

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `src/tradecraft/main.py`
- Modify: `src/tradecraft/runtime/kis_block_trader_runner.py`
- Modify: `src/tradecraft/runtime/investment_memory_runner.py`
- Test: `tests/test_daily_discovery_api.py`
- Test: `tests/test_api_smoke.py`

**Interfaces:**
- Produces: `settings.daily_discovery_etf_count`.
- Produces: discovery status config includes `etf_count`.
- Produces: latest context limits account for ETF count.

- [ ] Add failing tests for API config/limit behavior.
- [ ] Wire `etf_count` into app and runners.
- [ ] Run API and smoke focused tests.

### Task 4: Verification

**Files:**
- Verify only.

- [ ] Run `pytest tests/test_daily_discovery.py tests/test_symbol_analysis.py tests/test_daily_discovery_api.py -q`.
- [ ] Run `pytest tests/test_api_smoke.py -k daily_discovery -q`.
- [ ] Run `python -m py_compile src/tradecraft/services/daily_discovery.py src/tradecraft/services/naver_reports.py src/tradecraft/services/symbol_analysis.py src/tradecraft/main.py src/tradecraft/runtime/kis_block_trader_runner.py src/tradecraft/runtime/investment_memory_runner.py`.
