# Refactor Reference

This document is the refactoring index for HERMES/Jue. It turns the broader
specbook into implementation-grade guidance: which modules own which behavior,
which contracts must remain stable, and which tests should be run when a
subsystem changes.

## Refactor Goals

The project has evolved from a dashboard plus research helper into an active
multi-venue block-trading system. Refactors should reduce accidental coupling
without weakening the core loops:

- KIS Jue manages Korean equity and ETF blocks.
- Binance Jue manages crypto spot and futures blocks.
- Research pipelines populate durable evidence stores.
- Memory and reflection compress outcomes into reusable policies.
- Live performance/edge evaluation turns closed, adopted, failed, and
  unfilled outcomes into authority packets for safer manager sizing.
- Native Codex supplies high-reasoning structured decisions, not direct
  exchange access.
- Deterministic adapters, ledgers, rule executors, and safety gates decide what
  can become an order.

## Non-Negotiable Product Invariants

| Invariant | Meaning | Refactor Risk |
| --- | --- | --- |
| Active trading identity | Jue is a trading partner that can drive real block decisions. | Reintroducing passive helper wording or hiding execution state weakens operator trust. |
| Block independence | The same symbol can have multiple blocks with different thesis, horizon, target, stop, and entry trigger. | Symbol-level dedupe must not prevent a second valid block. |
| LLM intent only | Native Codex returns structured intent; service gates execute or reject it. | Giving LLM code paths direct exchange privileges bypasses auditability. |
| Durable ledgers | Blocks, orders, manager runs, quotes, reflections, and usage are persisted. | Treating DBs as cache destroys audit and learning loops. |
| Fail loud | LLM, exchange, parser, auth, precision, and rate failures must be recorded as errors. | Silent fallbacks create false confidence and bad trading feedback. |
| Venue separation | KIS and Binance share some process lessons but keep venue-specific memory, risk, and execution semantics. | Cross-venue leakage can make stock rules drive crypto trades or vice versa. |
| Live authority is bounded | Edge scorecards can restrict or allow capped scale, but never bypass hard execution gates. | Treating `scale_candidate` as direct order permission can defeat kill switches, cash/exposure limits, or exchange filters. |
| User-visible Korean | Internal reasoning can be English; user-facing UI/Telegram output must be Korean. | English notes in operator panes make Jue feel unfinished. |
| Safety gates outrank policy | Kill switch, auth, cash/quantity, duplicate-order prevention, leverage, precision, and exchange filters outrank memory policies. | Learned policies must not become hard execution bypasses. |

## Current Top-Level Modules

| Area | Files | Responsibility |
| --- | --- | --- |
| Control app | `src/tradecraft/main.py` | FastAPI app, static UI, service construction, API routes, admin auth, readiness, restart controls. |
| Settings | `src/tradecraft/config.py` | Environment aliases, defaults, readiness properties, live/paper toggles, paths, model settings, intervals. |
| Static UI | `src/tradecraft/web/static/index.html`, `app.js`, `style.css` | Operator dashboard, auth token storage, tab state, block boards, memory/research/settings panels. |
| KIS adapter | `src/tradecraft/services/kis.py` | KIS token cache, rate limiter, account, quotes, domestic order and order inquiry normalization. |
| KIS block trading | `src/tradecraft/services/kis_block_trader.py`, `src/tradecraft/runtime/kis_block_trader_runner.py` | KIS block ledger, LLM manager, adoption, rule tick, reconciliation, runner cadence. |
| Binance adapter | `src/tradecraft/services/binance.py` | Binance REST signing, spot/futures account, book ticker, exchange filters, order submission. |
| Binance block trading | `src/tradecraft/services/binance_block_trader.py`, `src/tradecraft/runtime/binance_block_trader_runner.py` | Crypto block ledger, spot/futures manager, rule tick, wallet adoption, performance reflection, Telegram reports. |
| Live performance/edge | `src/tradecraft/services/live_performance.py`, `src/tradecraft/services/live_edge.py`, `src/tradecraft/services/live_authority.py`, `src/tradecraft/runtime/live_evaluator_runner.py` | Attribution-aware performance rows, empirical edge scorecards, authority packets, evaluator runtime state. |
| Native Codex | `src/tradecraft/services/codex_native.py`, `codex_native_store.py`, `codex_instructions.py` | Native SDK execution, thread persistence, usage telemetry, workflow skill input, read-only sandbox control. |
| Jue skill registry | `src/tradecraft/jue/**`, `src/tradecraft/services/jue_skill_registry.py` | Skills, workflows, contracts, source manifest, prompt pack compilation. |
| Research | `naver_reports.py`, `rag_store.py`, `research_pipeline.py`, `symbol_fundamentals.py`, `etf_research.py`, `strategy_intelligence.py`, `daily_discovery.py` | Korean report ingestion, RAG, valuation, ETF research, strategy candidates, random discovery. |
| Crypto research | `crypto_market_research.py`, `crypto_quant.py`, `crypto_pattern_lab.py`, `crypto_alpha.py` | Crypto symbol universe, OHLCV/features, quant packets, pattern scorecards, external event alpha. |
| Memory | `investment_memory.py`, `investment_memory_runner.py`, `block_performance.py`, `jue_decision_packet.py`, `jue_lifecycle.py` | Persona, journals, policy rules, reflections, reviews, replays, lifecycle artifacts, prompt context packs. |
| Market context | `market_judgment.py`, `market_pulse.py`, related runners | KRX clock, account-aware market judge, investor/sector/index pulse, quote snapshots. |
| Telegram | `telegram.py`, `telegram_cli.py`, webhook in `main.py` | Operator commands and scheduled reports with chat/secret guards. |
| Reports microservice | `src/tradecraft/reports_api/**` | Optional report-console API and UI, saved views, report/RAG actions. |
| Backtest/replay | `src/tradecraft/backtest/**` | Time-travel simulation and replay scaffolding for weekly learning and future strategy evaluation. |

## Console Entrypoints

The package exposes these scripts in `pyproject.toml`:

| Entrypoint | Owner | Expected Role |
| --- | --- | --- |
| `tradecraft-ui`, `tradecraft-control` | `tradecraft.main:run` | Main FastAPI control app and static UI. |
| `tradecraft-runtime` | `tradecraft.runtime.runner:run` | Legacy runtime engine. |
| `tradecraft-intelligence` | `tradecraft.runtime.intelligence_runner:run` | Legacy/simple intelligence runner. |
| `tradecraft-strategy-insights` | `strategy_insights_runner` | Whale/세시반 style insight collection. |
| `tradecraft-market-judge` | `market_judge_runner` | KRX account-aware market judgment loop. |
| `tradecraft-market-pulse` | `market_pulse_runner` | KRX pulse collection loop. |
| `tradecraft-investment-memory` | `investment_memory_runner` | Rituals, reflections, reviews, replays, policy updates. |
| `tradecraft-live-evaluator` | `live_evaluator_runner` | Live performance/edge authority evaluator. |
| `tradecraft-crypto-market-research` | `crypto_market_research_runner` | Crypto market features and LLM notes. |
| `tradecraft-crypto-pattern-lab` | `crypto_pattern_lab_runner` | Freqtrade/pattern import and scorecards. |
| `tradecraft-crypto-alpha` | `crypto_alpha_runner` | Crypto external event alpha and outcomes. |
| `tradecraft-research` | `research_runner` | Legacy research snapshot/advice loop. |
| `tradecraft-kis-block-trader` | `kis_block_trader_runner` | Primary KIS block runner. |
| `tradecraft-binance-block-trader` | `binance_block_trader_runner` | Primary Binance block runner. |
| `tradecraft-naver-reports` | `naver_reports_runner` | Naver report crawl/sync loop. |
| `tradecraft-reports-api`, `tradecraft-reports-worker`, `tradecraft-reports-stack` | `reports_api` | Optional reports microservice stack. |

## Refactor Boundary Rules

### 1. API Layer vs Domain Services

`main.py` currently does too much. A future refactor can split route groups into
routers, but the API layer must remain responsible only for:

- request validation that is independent of domain state;
- admin/token/Telegram auth checks;
- converting domain failures into HTTP status codes;
- composing existing service outputs for UI convenience.

Domain services must continue to own exchange calls, ledger mutations, prompt
construction, rule execution, DB schema creation, migrations, and memory or
research context generation.

### 2. Prompt Construction vs Execution Gates

Prompt-building helpers can move into smaller modules, but order validation must
stay outside the LLM. A refactor is valid only if each candidate action still
flows through a deterministic validator before an order is written or sent.

### 3. Runtime Runner vs Service

Runner files should remain thin: build adapters/services from `AppSettings`,
loop on configured intervals, write compact runtime state JSON, and log cycle
status. Business logic belongs in service classes and repositories.

### 4. DB Repositories

Repositories are allowed to own SQLite schema and migrations. UI and API code
should consume repository/service methods, not open runtime DBs directly except
for read-only readiness summaries that are explicitly documented.

### 5. UI State

The static UI can stay framework-free, but all fetches should pass through the
central fetch helper so admin tokens, error banners, and auth modals behave
consistently. Refactors must preserve current-tab persistence and background
refresh behavior.

## Recommended Refactor Order

1. Split `main.py` into routers without changing endpoint paths.
2. Extract KIS prompt payload builders from `kis_block_trader.py`.
3. Extract Binance prompt payload builders from `binance_block_trader.py`.
4. Keep live authority packet construction in a small service boundary and pass
   venue packets into manager prompts through explicit fields.
5. Introduce typed repository/service return models for blocks, orders, and
   manager runs.
6. Make DB schema manifests machine-readable for docs and migrations.
7. Normalize runner state snapshot contracts.
8. Split UI panels by data domain while keeping one central state store.
9. Consolidate common block lifecycle code after KIS/Binance differences are
   explicitly modeled.

## Regression Test Map

| Change Area | Minimum Focused Tests |
| --- | --- |
| Admin auth, settings, readiness | `pytest tests/test_admin_auth.py tests/test_api_smoke.py tests/test_settings_api.py tests/test_process_status.py` |
| Native Codex | `pytest tests/test_codex_native.py tests/test_llm_usage.py tests/test_prompt_identity.py` |
| KIS adapter/block trader | `pytest tests/test_kis_adapter.py tests/test_kis_block_trader.py tests/test_kis_block_trader_runner.py tests/test_kis_trader_api.py` |
| Binance adapter/block trader | `pytest tests/test_binance_adapter.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_binance_trader_api.py tests/test_binance_risk.py` |
| Research/RAG/valuation/ETF | `pytest tests/test_naver_reports.py tests/test_rag_store.py tests/test_symbol_fundamentals.py tests/test_etf_research.py tests/test_strategy_intelligence.py tests/test_jue_research_spine.py` |
| Crypto research/quant/alpha | `pytest tests/test_crypto_market_research.py tests/test_crypto_quant.py tests/test_crypto_pattern_lab.py tests/test_crypto_alpha.py` |
| Memory/learning | `pytest tests/test_investment_memory.py tests/test_investment_memory_api.py tests/test_block_performance.py tests/test_jue_lifecycle.py` |
| Live performance/edge/authority | `pytest tests/test_live_performance.py tests/test_live_edge.py tests/test_live_authority.py tests/test_kr_equity_pattern_lab.py tests/test_live_evaluator_runner.py tests/test_api_smoke.py` |
| Market judge/pulse | `pytest tests/test_market_judgment.py tests/test_market_judge_runner.py tests/test_market_pulse.py` |
| UI | `pytest tests/test_static_ui.py && node --check src/tradecraft/web/static/app.js` |
| Spec/workflows | `pytest tests/test_docs_spec.py tests/test_jue_workflow_manifests.py tests/test_jue_skill_registry.py` |

## Refactor Acceptance Checklist

- Endpoint paths and response keys remain backward-compatible, or migration is
  documented in this specbook.
- Live/paper toggles still default to off for execution.
- All LLM failures are represented as explicit error records.
- No deterministic trading fallback is introduced for failed LLM calls.
- Native Codex remains read-only/deny-all unless a separate reviewed design
  changes the authority model.
- Runtime DB schema changes include migration behavior and tests.
- Memory policies remain soft preferences/cautions unless they are safety gates.
- Live authority packets remain visible to UI/ops and remain bounded by
  configured scale caps plus hard exchange/readiness gates.
- KIS and Binance memory scopes remain separated.
- UI surfaces stale/error states instead of swallowing them.
- Telegram output uses Korean names where available and includes enough context
  for the operator to identify the block or symbol.
