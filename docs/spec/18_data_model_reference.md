# Data Model Reference

HERMES uses local SQLite databases and runtime JSON files as the operational
source of truth. This document maps each store to its owner, schema purpose,
retention expectations, and refactor rules.

## Runtime Storage Principles

- Runtime DBs are not disposable cache unless explicitly marked as cache.
- A service that creates a DB schema owns migrations for that DB.
- API/UI code should use service/repository methods instead of opening DBs
  directly.
- Prompt snapshots and raw responses are provenance; compact them for storage
  when possible but do not silently rewrite history.
- Failed runs, failed orders, stale data, and missing evidence must be stored as
  first-class records.

## Primary Runtime Databases

| DB Path | Owner | Main Tables | Purpose |
| --- | --- | --- | --- |
| `.runtime/kis_blocks.db` | `KISBlockTrader` | `blocks`, `block_events`, `block_orders`, `manager_runs`, `quote_snapshots`, `reconciliation_runs`, `system_state` | KIS block ledger, order audit, quotes, reconciliation, kill/process state. |
| `.runtime/binance_blocks.db` | `BinanceBlockTrader` | `blocks`, `block_events`, `block_orders`, `manager_runs`, `quote_snapshots`, `block_performance_reflections`, `kill_switch` | Binance spot/futures block ledger, orders, quotes, performance, kill switch. |
| `.runtime/investment_memory.db` | `InvestmentMemoryService` | `memory_runs`, `daily_journals`, `memory_insights`, `memory_events`, `block_reflections`, `policy_scorecards`, `policy_rules`, `period_reviews`, `historical_replays`, `policy_revisions`, `policy_outcomes`, `symbol_analyses`, `jue_lifecycle_artifacts` | Jue memory, reflection, policy, replay, symbol-analysis, and lifecycle artifacts. |
| `.runtime/live_performance.db` | `LivePerformanceRepository` | `live_block_performance` | Attribution-aware block performance rows with cost-aware PnL and inclusion flags for Jue alpha, risk management, and execution quality. |
| `.runtime/live_edge.db` | `LiveEdgeRepository` | `live_edge_scorecards` | Venue/strategy/evidence scorecards with grade, sample, expectancy, rule-follow, execution-error, drawdown, and authority-multiplier fields. |
| `.runtime/codex_native_threads.db` | `CodexNativeStore` | `codex_threads`, `codex_turns`, `codex_account_checks`, `codex_model_checks`, `codex_thread_leases`, `codex_runtime_events` | Native Codex thread/session map, turn telemetry, account/model health, leases. |
| `.runtime/llm_usage.db` | `LLMUsageRepository` | `llm_calls` | LLM usage, latency, model, token estimates/exact usage, errors. |
| `.runtime/naver_reports.db` | `NaverReportRepository` | `reports`, `report_chunks`, `report_facts`, `report_symbol_links`, `symbol_directory` | Korean report metadata, text chunks, extracted facts, symbol links, name directory. |
| `.runtime/symbol_fundamentals.db` | `SymbolFundamentalsService` | `valuation_snapshots`, `financial_snapshots`, `valuation_scores` | Naver/WiseReport valuation, financials, scoring labels. |
| `.runtime/etf_research.db` | `ETFResearchRepository` | `etf_universe`, `etf_market_snapshots`, `etf_scores` | ETF universe, price/volume snapshots, relative ETF scoring. |
| `.runtime/strategy_insights.db` | `StrategyInsightRepository` | `strategy_signals` | Whale/세시반-style external strategy signals. |
| `.runtime/market_judgment.db` | `MarketJudgmentRepository` | `quote_snapshots`, `account_snapshots`, `judgment_runs`, `symbol_judgments` | KRX market clock/quotes/account-aware judgment records. |
| `.runtime/market_pulse.db` | `MarketPulseRepository` | `market_pulse_snapshots` | KRX index, investor, sector, and pulse context. |
| `.runtime/crypto_market_research.db` | `CryptoMarketResearchRepository` | `crypto_symbols`, `crypto_market_snapshots`, `crypto_klines`, `crypto_derivatives`, `crypto_features`, `crypto_regime_snapshots`, `crypto_external_context`, `crypto_research_runs`, `crypto_symbol_notes`, `crypto_candidates` | Crypto universe, OHLCV/features, derivatives, external context, LLM notes, candidates. |
| `.runtime/crypto_quant.db` | `CryptoQuantRepository` | `crypto_quant_signals`, `crypto_quant_signal_history`, `crypto_quant_outcomes` | Quant signals, history, outcome labeling. |
| `.runtime/crypto_pattern_lab.db` | `CryptoPatternLabRepository` | `freqtrade_strategy_sources`, `strategy_patterns`, `pattern_backtests`, `freqtrade_ohlcv_imports`, `optimization_runs`, `optimization_trials`, `optimized_strategy_sets` | Imported strategy patterns, local scorecards, bounded optimizer trials, and promoted optimized parameter sets. |
| `.runtime/kr_equity_pattern_lab.db` | `KREquityPatternLabRepository` | `kr_equity_pattern_lab_runs`, `optimized_strategy_sets` | KIS live-alpha grouped train/test summaries and venue-native optimized strategy sets for validation. |
| `.runtime/crypto_alpha.db` | `CryptoAlphaRepository` | `crypto_alpha_sources`, `crypto_alpha_snapshots`, `crypto_alpha_events`, `crypto_alpha_event_symbols`, `crypto_alpha_event_outcomes`, `crypto_alpha_hypotheses`, `crypto_alpha_context_cache` | Crypto news/event alpha and outcome memory. |
| `.runtime/jue_daily_discovery.db` | `DailyDiscoveryService` | `discovery_runs`, `discovery_samples` | Random KOSPI/KOSDAQ discovery and symbol study seeds. |
| `.runtime/portfolio_coach.db` | `PortfolioCoachRepository` | `portfolio_snapshots`, `advice_messages` | Legacy/auxiliary portfolio coaching snapshots. |
| `.runtime/kis_rate_limit.db` | `KISAdapter` | `rate_limits`, `access_tokens` | KIS REST pacing and OAuth token cache. |

## Runtime JSON State Files

| Path Setting | Default | Owner | Purpose |
| --- | --- | --- | --- |
| `TRADECRAFT_RUNTIME_STATE_PATH` | `.runtime/state.json` | Legacy runtime | Generic runtime state. |
| `TRADECRAFT_RESEARCH_STATE_PATH` | `.runtime/research.json` | Research runner | Research loop status and latest snapshot. |
| `TRADECRAFT_KIS_BLOCK_TRADER_STATE_PATH` | `.runtime/kis_block_trader.json` | KIS block runner | Runner cycle status, last tick, manager timing, ETF/discovery collection. |
| `TRADECRAFT_BINANCE_BLOCK_TRADER_STATE_PATH` | `.runtime/binance_block_trader.json` | Binance runner | Cycle status, quote/rule/manager timestamps, Telegram report state. |
| `TRADECRAFT_INVESTMENT_MEMORY_STATE_PATH` | `.runtime/investment_memory_runner.json` | Memory runner | Memory cycle status, ritual/review/replay/reflection summary. |
| `TRADECRAFT_LIVE_EVALUATOR_STATE_PATH` | `.runtime/live_evaluator.json` | Live evaluator runner | Latest evaluator cycle, performance summary, and KIS/Binance authority payload. |
| `TRADECRAFT_MARKET_JUDGE_STATE_PATH` | `.runtime/market_judge.json` | Market judge runner | Quote/LLM cadence status and run results. |
| `TRADECRAFT_MARKET_PULSE_STATE_PATH` | `.runtime/market_pulse.json` | Market pulse runner | Pulse collection status. |
| `TRADECRAFT_CRYPTO_MARKET_RESEARCH_STATE_PATH` | `.runtime/crypto_market_research.json` | Crypto research runner | Feature/LLM cycle status. |
| `TRADECRAFT_CRYPTO_PATTERN_LAB_STATE_PATH` | `.runtime/crypto_pattern_lab.json` | Pattern lab runner | Import/backtest cycle status. |
| `TRADECRAFT_CRYPTO_ALPHA_STATE_PATH` | `.runtime/crypto_alpha.json` | Crypto alpha runner | Crawl/outcome cycle status. |

These state files are read by readiness and UI. They should be compact enough
for frequent polling and should never contain secrets.

## KIS Block Ledger Schema Contract

`blocks`:

- `block_id` is the durable identity for one independent thesis.
- `symbol` is a 6-digit KRX code.
- `name` should be the best known display name and should not contain generic
  names such as only `정보` or the symbol itself when a directory name exists.
- `qty_initial` and `qty_open` are integer share counts.
- `entry_price`, `target_price`, and `stop_price` are KRW price levels.
- `created_by` distinguishes `llm`, `existing_position`, user/manual, and other
  adoption sources.
- `status` must stay within the service status vocabulary: `proposed`,
  `entry_pending`, `open`, `exit_pending`, `closed`, `paused`, `error`.
- `metadata_json` carries horizon, entry trigger, score, policy, user directive,
  adoption, and execution evidence.

`block_orders`:

- One block can have multiple order records.
- Pending orders must prevent duplicate submission until reconciled, canceled,
  stale, or failed.
- `limit_price` is integer KRW and should reflect tick-size rounding.
- `order_no`/`order_orgno` are exchange provenance.

`manager_runs`:

- Each LLM manager call stores prompt, response, parsed actions, workflow id,
  model, mode, status, and error.
- Failed LLM calls create `status='error'` rows with no synthetic trading
  action.

## Binance Block Ledger Schema Contract

`blocks`:

- `symbol` is a Binance symbol such as `BTCUSDT`.
- `market` is `spot` or `futures`.
- `side` is `long` for spot and can be `long` or `short` for futures.
- `qty_initial`/`qty_open` are decimal quantities subject to exchange filters.
- `leverage`, `margin_type`, and `liquidation_price` are futures-specific.
- `metadata_json` carries entry style, trigger price, horizon, sizing
  rationale, executable price structure, alpha/quant references, and Korean
  display notes.

`block_performance_reflections`:

- Use only filled/closed blocks for realized Jue performance.
- Preserve `gross_pnl_usdt`, `net_pnl_usdt`, `fee_usdt`, `funding_usdt`,
  `slippage_usdt`, and `cost_source`.
- Wallet-adopted assets should be managed but not counted as Jue-created alpha
  before adoption.

## Live Performance And Edge Contract

`live_block_performance`:

- Primary key is `(venue, block_id)`.
- `attribution` must distinguish Jue-created filled blocks from
  `adopted_existing_position`, `adopted_wallet_position`,
  `operational_failure_pre_fill`, and `unfilled_or_unrealized`.
- `include_in_jue_alpha`, `include_in_risk_management`, and
  `include_in_execution_quality` are explicit integer booleans. Reports and
  manager packets should use these flags instead of inferring inclusion only
  from `created_by`.
- `gross_pnl`, `net_pnl`, `cost_total`, and `pnl_pct` are normalized evaluator
  fields. They are useful for live authority and dashboards, but exchange fills,
  order inquiry, and account history remain the audit-grade source.
- `source_json` preserves the original block/order/reflection evidence used for
  the evaluator row.

`live_edge_scorecards`:

- Unique key is `(venue, strategy_family, evidence_key)`.
- `sample_count`, `expectancy_pct`, `win_rate`, `rule_follow_rate`,
  `execution_error_rate`, and `max_drawdown_pct` summarize empirical edge.
- `grade` controls live authority state. Current values are `observe_only`,
  `insufficient`, `restricted`, `qualified`, and `scale_candidate`.
- `authority_multiplier` is an input to the authority packet, not a direct order
  budget. The packet still applies configured caps and sample-size checks.
- `raw_json` preserves the full scorecard used to compute the grade.

The `/api/live/authority` payload composes these scorecards into venue packets:
`live_grade`, `max_budget_multiplier`, `allow_scale_up`, `scorecard_count`, and
the scorecards used. KIS and Binance managers should treat the venue packet as
the maximum evidence-backed sizing/scale authority for new or expanded blocks.
It cannot override kill switches, live/paper toggles, cash, quantity, exchange
filters, leverage, liquidation distance, duplicate-order guards, or
reconciliation.

## Memory Schema Contract

Memory data has two forms:

1. Markdown under `.runtime/investment_memory/` for human-readable persona,
   policies, journals, symbol notes, sector notes, regimes, and block notes.
2. SQLite rows in `.runtime/investment_memory.db` for provenance, status,
   deduplication, scorecards, revisions, and API/UI reads.

Important tables:

- `memory_runs`: every seed, ritual, update, reflection, review, replay, or
  policy operation.
- `daily_journals`: one row per trading day and slot.
- `memory_insights`: active/candidate observations by type and key.
- `memory_events`: pending closed/error/adoption events that require reflection.
- `block_reflections`: structured result and lesson per block.
- `policy_scorecards`: statistical evidence for policy candidates.
- `policy_rules`: versioned rules that Jue can consult.
- `period_reviews`: weekly/monthly performance summaries.
- `historical_replays`: as-of replay reviews.
- `policy_revisions`: candidate/active/rejected policy updates.
- `symbol_analyses`: instant and special-watch symbol analysis history.
- `jue_lifecycle_artifacts`: workflow-guided analyst artifacts.

Policy rows must not become hidden hard filters. They are inputs to Jue's
judgment and sizing/target/stop review unless they are explicit safety gates.

## Migration Rules

- Use `CREATE TABLE IF NOT EXISTS` for initial schemas.
- Use idempotent `ALTER TABLE` helpers for additive columns.
- Avoid destructive schema changes in-place. Create a new table, copy rows, and
  keep a migration test.
- Every new persisted JSON field needs an owner and a compaction limit if it can
  store prompt-sized payloads.
- Every DB with high-frequency writes needs retention rules or pruning tests.
- Any refactor that renames a table or column must update API serialization,
  tests, UI assumptions, and this document.

## Data Quality Flags

Refactors should preserve or add explicit status fields for:

- `ok`;
- `missing`;
- `stale`;
- `error`;
- `partial`;
- `unavailable`;
- `not_configured`;
- `skipped`;
- `disabled`.

Do not replace these with empty objects. Empty objects are ambiguous in trading
systems.
