# Current Inventory

## Repository Scope

Primary app code lives under `src/tradecraft`. Tests live under `tests`. Static UI lives under `src/tradecraft/web/static`.

Inventory was refreshed on 2026-06-30 with:

- `rg --files src/tradecraft | sort > /tmp/hermes_source_files.txt` (`260` files)
- `rg --files tests | sort > /tmp/hermes_test_files.txt` (`180` files)
- `rg --files docs | sort > /tmp/hermes_doc_files.txt` (`67` files)

Top-level source shape from the captured file list:

| Path | File Count | Notes |
| --- | ---: | --- |
| `src/tradecraft/backtest` | 9 | Backtest clock, data registry, engine, replay, scenarios, and simulated broker. |
| `src/tradecraft/api` | 37 | Router groups and payload builders split out of the control app. |
| `src/tradecraft/jue` | 46 | Jue workflow, skill, contract, and source-manifest packs. |
| `src/tradecraft/reports_api` | 12 | Reports microservice auth, API, ops, saved views, schemas, UI guard, worker, and stack runner. |
| `src/tradecraft/runtime` | 26 | Runtime process runners, process status/state files, runtime engine, and runner lifecycle helpers. |
| `src/tradecraft/services` | 105 | Exchange adapters, trading managers, research, memory, market context, strategy, telemetry, and operator services. |
| `src/tradecraft/web/static` | 22 | Static dashboard HTML, JavaScript modules, and CSS. |
| Root package files | 3 | `__init__.py`, `config.py`, and `main.py`. |

Current totals are roughly `260` source files, `180` test files, and `67` docs files. The test inventory covers API smoke tests, exchange adapters, block traders, runtime runners, research/memory services, reports microservice behavior, backtesting, strategy intelligence, settings, static UI contracts, Jue workflow packs, and trading-validation/authority behavior.

## Major Source Modules

| Area | Files | Responsibility |
| --- | --- | --- |
| App/API | `src/tradecraft/main.py`, `src/tradecraft/config.py` | FastAPI app setup, settings, route wiring, static UI serving, service API groups. |
| Reports API | `src/tradecraft/reports_api/main.py`, `src/tradecraft/reports_api/auth.py`, `src/tradecraft/reports_api/worker.py`, `src/tradecraft/reports_api/stack.py` | Separate reports console API, auth guards, saved views, ops/readiness, worker, and stack launcher. |
| Runtime Core | `src/tradecraft/runtime/runner.py`, `src/tradecraft/runtime/engine.py`, `src/tradecraft/runtime/process_status.py`, `src/tradecraft/runtime/state_store.py` | Shared runtime loop, process status/state files, broker/session/risk helpers. |
| Backtest | `src/tradecraft/backtest/engine.py`, `src/tradecraft/backtest/live_manager.py`, `src/tradecraft/backtest/runner.py`, `src/tradecraft/backtest/sim_broker.py` | Backtest clock, data registry, replay, scenarios, live manager, and simulated execution. |
| KIS | `src/tradecraft/services/kis.py`, `src/tradecraft/services/kis_block_trader.py` | KIS account, quotes, orders, block ledger, manager, executor, and block trading controls. |
| Binance | `src/tradecraft/services/binance.py`, `src/tradecraft/services/binance_block_trader.py`, `src/tradecraft/services/binance_risk.py` | Binance account, spot/futures orders, block ledger, manager, and exchange risk/precision controls. |
| Other Exchange Data | `src/tradecraft/services/bithumb.py`, `src/tradecraft/services/upbit.py`, `src/tradecraft/services/fx.py`, `src/tradecraft/services/market.py` | Market data and non-primary exchange adapters used by research or context layers. |
| Research | `src/tradecraft/services/naver_reports.py`, `src/tradecraft/services/rag_store.py`, `src/tradecraft/services/research_pipeline.py` | Report collection, vector/RAG store, research refresh, and citation-aware research pipeline. |
| Strategy | `src/tradecraft/services/strategy_intelligence.py`, `src/tradecraft/services/symbol_analysis.py`, `src/tradecraft/services/etf_research.py`, `src/tradecraft/services/daily_discovery.py`, `src/tradecraft/services/opportunity_scanner.py` | Candidate generation, valuation, ETF analysis, discovery, opportunity scanning, and strategy scoring. |
| Memory | `src/tradecraft/services/investment_memory.py`, `src/tradecraft/services/jue_decision_packet.py`, `src/tradecraft/services/block_performance.py` | Persona, journals, policy rules, reflections, context packs, and block performance learning. |
| Live Performance/Edge | `src/tradecraft/services/live_performance.py`, `src/tradecraft/services/live_edge.py`, `src/tradecraft/services/live_authority.py`, `src/tradecraft/runtime/live_evaluator_runner.py` | Attribution-aware block performance, empirical edge scorecards, venue authority packets, and evaluator state snapshots. |
| LLM/Telemetry | `src/tradecraft/services/codex_native.py`, `src/tradecraft/services/codex_native_store.py`, `src/tradecraft/services/llm_usage.py` | Native Codex request runtime, thread/session telemetry, model routing inputs, timeout/error handling, and call telemetry. |
| Market Context | `src/tradecraft/services/market_judgment.py`, `src/tradecraft/services/market_pulse.py`, `src/tradecraft/services/symbol_fundamentals.py` | KIS market judgment loop, macro/flow pulse, and symbol fundamentals/valuation snapshots. |
| Crypto Research | `src/tradecraft/services/crypto_market_research.py`, `src/tradecraft/services/crypto_quant.py`, `src/tradecraft/services/crypto_pattern_lab.py`, `src/tradecraft/services/crypto_alpha.py` | Crypto source collection, quant signals, pattern extraction/backtest inputs, and alpha DB. |
| UI | `src/tradecraft/web/static/index.html`, `src/tradecraft/web/static/app.js`, `src/tradecraft/web/static/style.css` | Static web dashboard and investment helper UI. |
| Telegram/Ops | `src/tradecraft/services/telegram.py`, `src/tradecraft/services/telegram_cli.py`, `src/tradecraft/services/runtime_bridge.py`, `src/tradecraft/services/runtime_maintenance.py`, `src/tradecraft/services/settings_catalog.py` | Bot/operator interaction, runtime process bridge, maintenance actions, and settings catalog. |

## Runtime Entrypoints

Entrypoints are from `[project.scripts]` in `pyproject.toml`.

| Entrypoint | Source | Role |
| --- | --- | --- |
| `tradecraft-ui` | `src/tradecraft/main.py` (`tradecraft.main:run`) | Alias for the main FastAPI/static UI process. |
| `tradecraft-control` | `src/tradecraft/main.py` (`tradecraft.main:run`) | Main control web app. |
| `tradecraft-runtime` | `src/tradecraft/runtime/runner.py` | Generic runtime runner. |
| `tradecraft-intelligence` | `src/tradecraft/runtime/intelligence_runner.py` | Intelligence runtime loop. |
| `tradecraft-strategy-insights` | `src/tradecraft/runtime/strategy_insights_runner.py` | Strategy signal/insights runner. |
| `tradecraft-market-judge` | `src/tradecraft/runtime/market_judge_runner.py` | KIS market judgment schedule. |
| `tradecraft-market-pulse` | `src/tradecraft/runtime/market_pulse_runner.py` | Korean market pulse collection. |
| `tradecraft-investment-memory` | `src/tradecraft/runtime/investment_memory_runner.py` | Memory rituals, reflections, policy updates. |
| `tradecraft-live-evaluator` | `src/tradecraft/runtime/live_evaluator_runner.py` | Live performance/edge evaluator and authority packet state writer. |
| `tradecraft-crypto-market-research` | `src/tradecraft/runtime/crypto_market_research_runner.py` | Crypto research collection. |
| `tradecraft-crypto-pattern-lab` | `src/tradecraft/runtime/crypto_pattern_lab_runner.py` | Crypto strategy pattern ingestion/backtest. |
| `tradecraft-crypto-alpha` | `src/tradecraft/runtime/crypto_alpha_runner.py` | Crypto alpha scoring and DB update. |
| `tradecraft-research` | `src/tradecraft/runtime/research_runner.py` | Research refresh runner. |
| `tradecraft-kis-block-trader` | `src/tradecraft/runtime/kis_block_trader_runner.py` | KIS block manager/executor runtime. |
| `tradecraft-binance-block-trader` | `src/tradecraft/runtime/binance_block_trader_runner.py` | Binance spot/futures block runtime. |
| `tradecraft-naver-reports` | `src/tradecraft/runtime/naver_reports_runner.py` | Naver report collection runner. |
| `tradecraft-reports-api` | `src/tradecraft/reports_api/main.py` | Reports microservice API. |
| `tradecraft-reports-worker` | `src/tradecraft/reports_api/worker.py` | Reports worker process. |
| `tradecraft-reports-stack` | `src/tradecraft/reports_api/stack.py` | Reports API/worker stack launcher. |

Runtime inventory is intentionally not pinned to PID values. Active process state is mutable and should be read from `/api/ops/readiness`, `.runtime/pids/*.pid`, and the process-status helpers at runtime instead of copied into the spec as fixed numbers.

## Top-Level Runtime DBs

DB names and table names were captured with `for db in .runtime/*.db; do sqlite3 "$db" ".tables"; done`. This table intentionally covers top-level `.runtime/*.db` files; nested SQLite stores such as `.runtime/rag_chroma/chroma.sqlite3`, `.runtime/marketdata/candles.db`, and `.runtime/naver_reports/reports.db` should be documented in the DB appendix when they become refactor-relevant.

| DB | Tables | Role |
| --- | --- | --- |
| `.runtime/kis_blocks.db` | `blocks`, `block_events`, `block_orders`, `manager_runs`, `quote_snapshots`, `reconciliation_runs`, `system_state` | KIS block ledger, orders, events, quotes, manager runs, and reconciliation state. |
| `.runtime/binance_blocks.db` | `blocks`, `block_events`, `block_orders`, `manager_runs`, `quote_snapshots`, `kill_switch`, `block_performance_reflections` | Binance block ledger, spot/futures order history, runtime kill switch, quotes, manager runs, and performance reflections. |
| `.runtime/investment_memory.db` | `block_reflections`, `daily_journals`, `memory_events`, `memory_insights`, `memory_runs`, `period_reviews`, `policy_changes`, `policy_outcomes`, `policy_revisions`, `policy_rules`, `policy_scorecards`, `symbol_analyses`, `telegram_sends` | Memory runs, journals, insights, block reflections, policy rules/revisions, scorecards, symbol analyses, and Telegram send tracking. |
| `.runtime/live_performance.db` | `live_block_performance` | Venue/block performance attribution with cost-aware PnL fields and inclusion flags for Jue alpha, risk management, and execution quality. |
| `.runtime/live_edge.db` | `live_edge_scorecards` | Venue, strategy-family, and evidence-key scorecards that produce live authority grades and budget multipliers. |
| `.runtime/market_judgment.db` | `account_snapshots`, `judgment_runs`, `quote_snapshots`, `symbol_judgments` | KIS account snapshots, quote snapshots, market judgment runs, and symbol judgments. |
| `.runtime/market_pulse.db` | `market_pulse_snapshots` | Korean market regime/pulse snapshots. |
| `.runtime/naver_reports.db` | `report_chunks`, `report_facts`, `report_symbol_links`, `reports`, `symbol_directory` | Naver report metadata, chunk/fact corpus, symbol links, and symbol directory. |
| `.runtime/symbol_fundamentals.db` | `financial_snapshots`, `valuation_scores`, `valuation_snapshots` | Financial snapshots and valuation score/snapshot data. |
| `.runtime/etf_research.db` | `etf_market_snapshots`, `etf_scores`, `etf_universe` | ETF universe, market snapshots, and scores. |
| `.runtime/llm_usage.db` | `llm_calls` | LLM usage telemetry. |
| `.runtime/crypto_market_research.db` | `crypto_candidates`, `crypto_derivatives`, `crypto_external_context`, `crypto_features`, `crypto_klines`, `crypto_market_snapshots`, `crypto_regime_snapshots`, `crypto_research_runs`, `crypto_symbol_notes`, `crypto_symbols` | Crypto source collection, normalized market context, symbols, candidates, derivatives, klines, and research runs. |
| `.runtime/crypto_quant.db` | `crypto_quant_outcomes`, `crypto_quant_signal_history`, `crypto_quant_signals` | Crypto quant signal snapshots, history, and outcomes. |
| `.runtime/crypto_pattern_lab.db` | `freqtrade_ohlcv_imports`, `freqtrade_strategy_sources`, `pattern_backtests`, `strategy_patterns` | Imported public strategy patterns, OHLCV imports, and pattern backtests. |
| `.runtime/crypto_alpha.db` | `crypto_alpha_context_cache`, `crypto_alpha_event_outcomes`, `crypto_alpha_event_symbols`, `crypto_alpha_events`, `crypto_alpha_hypotheses`, `crypto_alpha_snapshots`, `crypto_alpha_sources` | Crypto alpha events, hypotheses, sources, context cache, snapshots, symbols, and outcomes. |
| `.runtime/codex_native_threads.db` | `codex_account_checks`, `codex_model_checks`, `codex_runtime_events`, `codex_thread_leases`, `codex_threads`, `codex_turns` | Native Codex thread/session state, leases, turns, model checks, and runtime events. |
| `.runtime/jue_daily_discovery.db` | `discovery_runs`, `discovery_samples` | Daily discovery run records and sampled opportunities. |
| `.runtime/kis_rate_limit.db` | `access_tokens`, `rate_limits` | KIS token and rate-limit state. |
| `.runtime/kr_equity_pattern_lab.db` | `kr_equity_pattern_lab_runs`, `optimized_strategy_sets` | Korean equity pattern lab runs and optimized strategy sets. |
| `.runtime/portfolio_coach.db` | `advice_messages`, `portfolio_snapshots` | Portfolio coach snapshots and advice messages. |
| `.runtime/strategy_insights.db` | `strategy_signals` | Strategy signal output. |
| `.runtime/strategy_intelligence.db` | currently empty or schema-pending in some installs | Legacy strategy-intelligence placeholder; prefer `strategy_insights.db` and current Jue research/decision packets. |
| `.runtime/trading_validation.db` | `validation_runs` | 19-discipline validation runs and venue readiness diagnostics. |
| `.runtime/watchdog_events.db` | `watchdog_events` | Watchdog observations and restart/health events. |

## Known Inventory Caveats

- The worktree is currently dirty; this inventory should not be read as a clean-branch baseline, and unrelated changed/untracked files are intentionally not enumerated here.
- Runtime DBs are local operational state and may be large.
- `.runtime` includes current process state, logs, JSON status files, DB WAL/SHM sidecars, repair backups, and backtest output JSON files.
- Temporary `test_*.db` or `tmp_*.db` runtime files are cleanup candidates rather than canonical inventory entries when present.
- `.runtime/pids` may contain PID files, but PID values are point-in-time process metadata and must be read live.
- Some historical records preserve older prompt wording or legacy assumptions.
- Binance wallet adoption records must be separated from 쥬-created block performance.
- Live performance/edge DBs are evaluator outputs, not replacements for exchange fills, KIS/Binance block ledgers, or investment-memory reflections.
