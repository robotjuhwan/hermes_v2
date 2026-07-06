# Operations Runbook

This runbook describes how to operate and verify HERMES/Jue after refactors,
configuration changes, restarts, or trading incidents.

## Local Environment

Expected setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Primary test command:

```bash
pytest
```

Focused checks should be run first when touching one subsystem.

## Required Environment Groups

### Admin/Auth

- `TRADECRAFT_ADMIN_TOKEN` or `TRADECRAFT_ADMIN_TOKENS`
- `TRADECRAFT_TELEGRAM_BOT_TOKEN`
- `TRADECRAFT_TELEGRAM_CHAT_ID`
- `TRADECRAFT_TELEGRAM_WEBHOOK_SECRET` when webhook mode is used

### Native Codex

- `TRADECRAFT_CODEX_NATIVE_MODE=sdk`
- `TRADECRAFT_LLM_MODEL`
- `TRADECRAFT_LLM_REASONING_EFFORT=xhigh`
- `TRADECRAFT_CODEX_NATIVE_TIMEOUT_MS`
- `TRADECRAFT_CODEX_NATIVE_THREAD_MODE`
- `TRADECRAFT_CODEX_NATIVE_COMPACT_AFTER_TURNS`

### KIS

- `KIS_PRIMARY_APP_KEY`
- `KIS_PRIMARY_APP_SECRET`
- `KIS_PRIMARY_ACCOUNT_NO`
- `KIS_PRIMARY_PRODUCT_CODE`
- `TRADECRAFT_KIS_BLOCK_TRADER_ENABLED`
- `TRADECRAFT_KIS_BLOCK_TRADER_EXECUTE_ORDERS`
- KIS rate-limit/token-cache settings when live

### Binance

- `BINANCE_SPOT_API_KEY`, `BINANCE_SPOT_API_SECRET`
- `BINANCE_FUTURES_API_KEY`, `BINANCE_FUTURES_API_SECRET`
- `TRADECRAFT_BINANCE_BLOCK_TRADER_ENABLED`
- `TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_SPOT_ORDERS`
- `TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_FUTURES_ORDERS`
- model, interval, universe, risk, and budget settings

### Research/Memory

- Naver report settings
- RAG settings
- valuation/ETF settings
- crypto research/quant/pattern/alpha settings
- `TRADECRAFT_INVESTMENT_MEMORY_ENABLED`

### Live Evaluation

- `TRADECRAFT_LIVE_EVALUATOR_ENABLED`
- `TRADECRAFT_LIVE_EVALUATOR_DB_PATH`
- `TRADECRAFT_LIVE_PERFORMANCE_DB_PATH`
- `TRADECRAFT_LIVE_EVALUATOR_STATE_PATH`
- `TRADECRAFT_LIVE_EVALUATOR_INTERVAL_SEC`
- `TRADECRAFT_LIVE_AUTHORITY_MAX_SCALE_MULTIPLIER`
- `TRADECRAFT_LIVE_AUTHORITY_MIN_SAMPLES_TO_SCALE`

Secrets must stay in local env files or process environment. Do not copy secret
values into this spec, tests, logs, or committed files.

## Process Set

Recommended active process set for full operation:

- `tradecraft-control`
- `tradecraft-kis-block-trader`
- `tradecraft-binance-block-trader`
- `tradecraft-investment-memory`
- `tradecraft-live-evaluator`
- `tradecraft-market-judge`
- `tradecraft-market-pulse`
- `tradecraft-naver-reports`
- `tradecraft-crypto-market-research`
- `tradecraft-crypto-pattern-lab`
- `tradecraft-crypto-alpha`
- `tradecraft-strategy-insights`

Optional/legacy:

- `tradecraft-runtime`
- `tradecraft-research`
- reports microservice stack

## Restart Contract

After code or critical config changes:

1. Restart `tradecraft-control`.
2. Restart `tradecraft-investment-memory` and `tradecraft-live-evaluator` when
   reflection, performance, edge, or authority behavior changed.
3. Restart KIS/Binance runners whose code/config changed.
4. Restart research/memory runners if prompt, schema, or DB behavior changed.
5. Call `GET /api/ops/readiness`.
6. Call `GET /api/live/authority`.
7. Call `GET /api/codex/native/status`.
8. Confirm UI banners do not show stale process, runner stopped, missing token,
   or restart required.

The UI restart endpoint is:

```http
POST /api/ops/restart
```

It is admin-token protected and should be used only for known process keys.

## Readiness Checklist

`GET /api/ops/readiness` should answer:

- process status for core runners;
- code staleness;
- admin token configured;
- KIS readiness;
- Binance readiness;
- native Codex readiness;
- Telegram readiness;
- live/paper status;
- live evaluator status and `/api/live/authority` pointer;
- kill switch status;
- memory status;
- market judge/pulse status;
- next manager/judge schedule where known.

Green readiness does not guarantee profit. It guarantees that the operating
components are connected, fresh, and not obviously blocked.

`GET /api/live/authority` should answer:

- evaluator enabled flag;
- live edge DB status;
- live performance DB summary;
- max scale multiplier and minimum samples config;
- KIS authority packet;
- Binance authority packet.

Each venue packet should show `live_grade`, `max_budget_multiplier`,
`allow_scale_up`, `scorecard_count`, and the scorecards used. Empty scorecards
default to `observe_only` authority and should be treated as missing edge
evidence, not as proof that trading is safe.

## Native Codex Health

Check:

```http
GET /api/codex/native/status
POST /api/codex/native/check
GET /api/llm/status
POST /api/llm/probe
GET /api/llm/usage/status
GET /api/llm/usage/summary
```

Expected:

- mode is `sdk`;
- intended model is visible;
- reasoning effort is visible;
- recent account/model checks are ok;
- usage telemetry records successful and failed calls;
- failed calls show explicit errors.

## KIS Daily Operating Flow

Before open:

- KIS account ready.
- KIS block runner running.
- Market judge and pulse running.
- Memory pre-open ritual completed or intentionally skipped.
- Live evaluator running and KIS authority packet visible.
- Open/adopted blocks have valid target/stop/horizon.
- Waiting-entry blocks have valid trigger metadata.

During market:

- Rule tick active.
- Manager cadence active.
- Quotes fresh.
- Reconciliation not stuck.
- New blocks are not only chasing highs unless Jue's thesis and execution
  structure justify it.
- User directives are reflected in block metadata or memory.

After close:

- Closed/error blocks queued for reflection.
- Post-close journal generated.
- Performance, live edge, authority, and policy scorecards updated where due.

## Binance Daily Operating Flow

Because Binance is 24h, use scheduled operator checkpoints:

- 06:00 report;
- 12:00 report;
- 20:00 report.

During checks:

- Spot and futures readiness match intended live settings.
- Live evaluator running and Binance authority packet visible.
- Manager interval matches intended cadence.
- Crypto research/quant/pattern/alpha are fresh.
- Watch notes are Korean in UI/Telegram.
- Order/filter errors are visible.
- Realized Jue block PnL excludes pre-adoption wallet PnL.
- Spot is not starved if spot execution is intended.

## Incident Review Procedure

For any suspicious trade:

1. Identify block id.
2. Read block row, events, orders, quote snapshots, and manager run.
3. Check exchange/account reconciliation evidence.
4. Check memory context and active policies used by that manager run.
5. Check the venue live authority packet and underlying live performance/edge rows.
6. Check research/quant/alpha packet freshness.
7. Classify issue:
   - strategy issue;
   - evidence issue;
   - execution price issue;
   - exchange precision/filter issue;
   - stale data issue;
   - LLM error;
   - UI display issue;
   - operator configuration issue.
8. Create or run reflection.
9. If repeated, convert to policy revision, edge restriction, or code fix.

## Verification Commands By Scope

### Static/Docs

```bash
pytest tests/test_docs_spec.py tests/test_jue_workflow_manifests.py tests/test_jue_skill_registry.py
node --check src/tradecraft/web/static/app.js
git diff --check
```

### KIS

```bash
pytest tests/test_kis_adapter.py tests/test_kis_block_trader.py tests/test_kis_block_trader_runner.py tests/test_kis_trader_api.py
```

### Binance

```bash
pytest tests/test_binance_adapter.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_binance_trader_api.py tests/test_binance_risk.py
```

### Research

```bash
pytest tests/test_naver_reports.py tests/test_rag_store.py tests/test_symbol_fundamentals.py tests/test_etf_research.py tests/test_strategy_intelligence.py tests/test_jue_research_spine.py
```

### Memory

```bash
pytest tests/test_investment_memory.py tests/test_investment_memory_api.py tests/test_block_performance.py tests/test_live_performance.py tests/test_live_edge.py tests/test_live_authority.py tests/test_live_evaluator_runner.py tests/test_jue_lifecycle.py
```

### Full Smoke

```bash
pytest tests/test_api_smoke.py tests/test_static_ui.py tests/test_codex_native.py tests/test_llm_usage.py tests/test_live_authority.py
```

## Refactor Handoff Template

When handing off a refactor, include:

- changed files;
- DB migrations;
- API compatibility notes;
- runner restart requirements;
- test commands and results;
- operational risk notes;
- whether live execution settings were touched;
- whether memory/policy behavior changed.
