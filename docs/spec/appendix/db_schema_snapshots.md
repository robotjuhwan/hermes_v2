# DB Schema Snapshots

Captured from the Task 4 top-level runtime DB files listed below on 2026-05-24 using Python stdlib `sqlite3` read-only inspection. This is a concise refactor aid, not a full raw `.schema` dump. Other top-level operational DBs, including portfolio coach, strategy insights, KIS rate limit, temporary test, and preview DBs, are outside this appendix unless they become refactor-relevant.

## Key `CREATE TABLE` Excerpts

The snippets below preserve the create-level shape of the highest-risk tables. They are descriptive schema snapshots, not migration scripts.

```sql
-- .runtime/kis_blocks.db
CREATE TABLE blocks (block_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', qty_initial INTEGER NOT NULL, qty_open INTEGER NOT NULL DEFAULT 0, entry_price REAL, target_price REAL, stop_price REAL, thesis TEXT NOT NULL DEFAULT '', llm_reason TEXT NOT NULL DEFAULT '', risk_note TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT 'llm', manager_run_id INTEGER, status TEXT NOT NULL, force_exit_requested INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, opened_at TEXT NOT NULL DEFAULT '', closed_at TEXT NOT NULL DEFAULT '');
CREATE TABLE block_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, block_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL, limit_price INTEGER NOT NULL DEFAULT 0, order_type TEXT NOT NULL DEFAULT '00', status TEXT NOT NULL, order_no TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', response_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, order_orgno TEXT NOT NULL DEFAULT '', filled_qty INTEGER NOT NULL DEFAULT 0, remaining_qty INTEGER NOT NULL DEFAULT 0, avg_fill_price REAL, last_checked_at TEXT NOT NULL DEFAULT '', cancel_requested INTEGER NOT NULL DEFAULT 0, cancel_order_no TEXT NOT NULL DEFAULT '', cancel_response_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE manager_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL, market_session TEXT NOT NULL, status TEXT NOT NULL, mode TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', prompt_json TEXT NOT NULL DEFAULT '{}', response_json TEXT NOT NULL DEFAULT '{}', actions_json TEXT NOT NULL DEFAULT '{}');

-- .runtime/binance_blocks.db
CREATE TABLE blocks (block_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, market TEXT NOT NULL DEFAULT 'spot', side TEXT NOT NULL DEFAULT 'long', qty_initial REAL NOT NULL, qty_open REAL NOT NULL DEFAULT 0, entry_price REAL, target_price REAL, stop_price REAL, leverage INTEGER NOT NULL DEFAULT 1, margin_type TEXT NOT NULL DEFAULT '', liquidation_price REAL, thesis TEXT NOT NULL DEFAULT '', llm_reason TEXT NOT NULL DEFAULT '', risk_note TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT 'llm', manager_run_id INTEGER, status TEXT NOT NULL, force_exit_requested INTEGER NOT NULL DEFAULT 0, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, opened_at TEXT NOT NULL DEFAULT '', closed_at TEXT NOT NULL DEFAULT '');
CREATE TABLE block_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, block_id TEXT NOT NULL, symbol TEXT NOT NULL, market TEXT NOT NULL DEFAULT 'spot', side TEXT NOT NULL, qty REAL NOT NULL, order_type TEXT NOT NULL DEFAULT 'MARKET', status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', response_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE block_performance_reflections (block_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, market TEXT NOT NULL DEFAULT 'spot', side TEXT NOT NULL DEFAULT 'long', entry_price REAL NOT NULL DEFAULT 0, exit_price REAL NOT NULL DEFAULT 0, stop_price REAL NOT NULL DEFAULT 0, target_price REAL NOT NULL DEFAULT 0, pnl_usdt REAL NOT NULL DEFAULT 0, r_multiple REAL NOT NULL DEFAULT 0, lesson_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, mfe_r_multiple REAL NOT NULL DEFAULT 0, mae_r_multiple REAL NOT NULL DEFAULT 0, pattern_key TEXT NOT NULL DEFAULT '');

-- .runtime/investment_memory.db
CREATE TABLE memory_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL, kind TEXT NOT NULL, slot TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, mode TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', input_json TEXT NOT NULL DEFAULT '{}', output_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE block_reflections (block_id TEXT PRIMARY KEY, symbol TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', exit_reason TEXT NOT NULL DEFAULT '', pnl_krw REAL NOT NULL DEFAULT 0, pnl_pct REAL NOT NULL DEFAULT 0, mfe_pct REAL NOT NULL DEFAULT 0, mae_pct REAL NOT NULL DEFAULT 0, hold_seconds INTEGER NOT NULL DEFAULT 0, rule_followed INTEGER NOT NULL DEFAULT 0, lesson_md TEXT NOT NULL DEFAULT '', metrics_json TEXT NOT NULL DEFAULT '{}', source_run_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE policy_rules (policy_id TEXT NOT NULL, version INTEGER NOT NULL, rule_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'candidate', action TEXT NOT NULL DEFAULT 'observe', condition_json TEXT NOT NULL DEFAULT '{}', effect_json TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '', evidence_json TEXT NOT NULL DEFAULT '{}', source_scorecard_json TEXT NOT NULL DEFAULT '{}', file_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, activated_at TEXT NOT NULL DEFAULT '', retired_at TEXT NOT NULL DEFAULT '', PRIMARY KEY(policy_id, version));

-- .runtime/market_judgment.db
CREATE TABLE judgment_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL, market_session TEXT NOT NULL, status TEXT NOT NULL, mode TEXT NOT NULL, model TEXT NOT NULL, query TEXT NOT NULL, error_message TEXT NOT NULL DEFAULT '', prompt_json TEXT NOT NULL DEFAULT '{}', response_json TEXT NOT NULL DEFAULT '{}', source_snapshot_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE symbol_judgments (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL, stance TEXT NOT NULL, account_action TEXT NOT NULL, horizon TEXT NOT NULL, confidence REAL, reasons_json TEXT NOT NULL DEFAULT '[]', risks_json TEXT NOT NULL DEFAULT '[]', triggers_json TEXT NOT NULL DEFAULT '[]', data_gaps_json TEXT NOT NULL DEFAULT '[]', quote_json TEXT NOT NULL DEFAULT '{}', position_json TEXT NOT NULL DEFAULT '{}', strategy_json TEXT NOT NULL DEFAULT '{}', FOREIGN KEY(run_id) REFERENCES judgment_runs(id));

-- .runtime/market_pulse.db
CREATE TABLE market_pulse_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL, trading_day TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, regime TEXT NOT NULL DEFAULT '', score REAL NOT NULL DEFAULT 0, indices_json TEXT NOT NULL DEFAULT '[]', sector_json TEXT NOT NULL DEFAULT '{}', block_alignment_json TEXT NOT NULL DEFAULT '[]', risk_flags_json TEXT NOT NULL DEFAULT '[]', data_gaps_json TEXT NOT NULL DEFAULT '[]', raw_json TEXT NOT NULL DEFAULT '{}');

-- .runtime/naver_reports.db
CREATE TABLE reports (report_id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'unknown', source_url TEXT NOT NULL, detail_url TEXT NOT NULL, pdf_url TEXT NOT NULL UNIQUE, title TEXT NOT NULL, company_name TEXT NOT NULL DEFAULT '', broker TEXT NOT NULL, analyst TEXT NOT NULL DEFAULT '', symbol TEXT NOT NULL, published_at TEXT NOT NULL, crawled_at TEXT NOT NULL DEFAULT '', pdf_sha256 TEXT NOT NULL DEFAULT '', pdf_archived_path TEXT NOT NULL DEFAULT '', content_source TEXT NOT NULL DEFAULT 'pdf_extract', content TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE report_symbol_links (report_id INTEGER NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', asset_class TEXT NOT NULL DEFAULT 'stock', link_type TEXT NOT NULL DEFAULT 'mention', source TEXT NOT NULL DEFAULT 'unknown', confidence REAL NOT NULL DEFAULT 0.0, evidence TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (report_id, symbol, link_type), FOREIGN KEY (report_id) REFERENCES reports(report_id) ON DELETE CASCADE);

-- .runtime/symbol_fundamentals.db
CREATE TABLE valuation_snapshots (snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', price INTEGER, market_cap_krw INTEGER, per REAL, eps INTEGER, pbr REAL, bps INTEGER, dividend_yield_pct REAL, industry_per REAL, industry_name TEXT NOT NULL DEFAULT '', as_of TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '', raw_json TEXT NOT NULL DEFAULT '{}', crawled_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ok', error_message TEXT NOT NULL DEFAULT '', last_attempt_at TEXT NOT NULL DEFAULT '', UNIQUE(symbol, as_of));
CREATE TABLE valuation_scores (symbol TEXT PRIMARY KEY, undervalued_score INTEGER NOT NULL DEFAULT 0, overvalued_risk INTEGER NOT NULL DEFAULT 0, quality_score INTEGER NOT NULL DEFAULT 0, growth_score INTEGER NOT NULL DEFAULT 0, relative_per_discount_pct REAL, pbr_roe_fit REAL, label TEXT NOT NULL DEFAULT 'unknown', reasons_json TEXT NOT NULL DEFAULT '[]', risks_json TEXT NOT NULL DEFAULT '[]', scored_at TEXT NOT NULL);

-- .runtime/etf_research.db
CREATE TABLE etf_universe (symbol TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, tags_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE etf_scores (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, label TEXT NOT NULL, liquidity_score REAL NOT NULL, momentum_score REAL NOT NULL, core_fit_score REAL NOT NULL, risk_score REAL NOT NULL, reasons_json TEXT NOT NULL, risks_json TEXT NOT NULL, scored_at TEXT NOT NULL);

-- .runtime/llm_usage.db
CREATE TABLE llm_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL, trading_day TEXT NOT NULL, component TEXT NOT NULL, operation TEXT NOT NULL DEFAULT '', model TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, latency_ms INTEGER NOT NULL DEFAULT 0, prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0, total_tokens INTEGER NOT NULL DEFAULT 0, usage_source TEXT NOT NULL DEFAULT 'missing', input_chars INTEGER NOT NULL DEFAULT 0, output_chars INTEGER NOT NULL DEFAULT 0, error_message TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}');

-- .runtime/crypto_market_research.db
CREATE TABLE crypto_symbols (symbol TEXT PRIMARY KEY, base_asset TEXT NOT NULL DEFAULT '', quote_asset TEXT NOT NULL DEFAULT '', spot_enabled INTEGER NOT NULL DEFAULT 0, futures_enabled INTEGER NOT NULL DEFAULT 0, liquidity_tier TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
CREATE TABLE crypto_klines (symbol TEXT NOT NULL, market TEXT NOT NULL DEFAULT 'spot', interval TEXT NOT NULL, open_time INTEGER NOT NULL, open REAL NOT NULL DEFAULT 0, high REAL NOT NULL DEFAULT 0, low REAL NOT NULL DEFAULT 0, close REAL NOT NULL DEFAULT 0, volume REAL NOT NULL DEFAULT 0, quote_volume REAL NOT NULL DEFAULT 0, close_time INTEGER NOT NULL DEFAULT 0, raw_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(symbol, market, interval, open_time));
CREATE TABLE crypto_candidates (symbol TEXT PRIMARY KEY, market TEXT NOT NULL DEFAULT 'spot', stance TEXT NOT NULL DEFAULT '', horizon TEXT NOT NULL DEFAULT '', score REAL NOT NULL DEFAULT 0, confidence REAL NOT NULL DEFAULT 0, reason_md TEXT NOT NULL DEFAULT '', block_template_json TEXT NOT NULL DEFAULT '{}', source_run_id INTEGER, updated_at TEXT NOT NULL);

-- .runtime/crypto_quant.db
CREATE TABLE crypto_quant_signals (symbol TEXT NOT NULL, horizon TEXT NOT NULL, long_score REAL NOT NULL DEFAULT 0, short_score REAL NOT NULL DEFAULT 0, no_trade_score REAL NOT NULL DEFAULT 0, expected_r_long REAL NOT NULL DEFAULT 0, expected_r_short REAL NOT NULL DEFAULT 0, signal_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL, PRIMARY KEY (symbol, horizon));
CREATE TABLE crypto_quant_outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, side TEXT NOT NULL, horizon TEXT NOT NULL, source_id TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL DEFAULT '', r_multiple REAL NOT NULL DEFAULT 0, mfe_r REAL NOT NULL DEFAULT 0, mae_r REAL NOT NULL DEFAULT 0, payload_json TEXT NOT NULL DEFAULT '{}', labeled_at TEXT NOT NULL);

-- .runtime/crypto_pattern_lab.db
CREATE TABLE strategy_patterns (pattern_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, name TEXT NOT NULL, family TEXT NOT NULL, direction TEXT NOT NULL, timeframe TEXT NOT NULL, indicators_json TEXT NOT NULL DEFAULT '[]', expression_json TEXT NOT NULL DEFAULT '{}', risk_tags_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
CREATE TABLE pattern_backtests (id INTEGER PRIMARY KEY AUTOINCREMENT, pattern_id TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL, sample_start TEXT NOT NULL DEFAULT '', sample_end TEXT NOT NULL DEFAULT '', trade_count INTEGER NOT NULL DEFAULT 0, win_rate REAL NOT NULL DEFAULT 0, expectancy_r REAL NOT NULL DEFAULT 0, avg_r REAL NOT NULL DEFAULT 0, profit_factor REAL NOT NULL DEFAULT 0, max_loss_r REAL NOT NULL DEFAULT 0, mfe_r REAL NOT NULL DEFAULT 0, mae_r REAL NOT NULL DEFAULT 0, regime TEXT NOT NULL DEFAULT '', score REAL NOT NULL DEFAULT 0, warnings_json TEXT NOT NULL DEFAULT '[]', evaluated_at TEXT NOT NULL);

-- .runtime/crypto_alpha.db
CREATE TABLE crypto_alpha_sources (source_id TEXT PRIMARY KEY, label TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL DEFAULT '', trust_score REAL NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1, last_crawled_at TEXT NOT NULL DEFAULT '', last_status TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
CREATE TABLE crypto_alpha_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id INTEGER, source_id TEXT NOT NULL, event_type TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', event_time TEXT NOT NULL DEFAULT '', detected_at TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0, importance REAL NOT NULL DEFAULT 0, decay_hours REAL NOT NULL DEFAULT 72, status TEXT NOT NULL DEFAULT 'active', raw_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE crypto_alpha_hypotheses (hypothesis_id INTEGER PRIMARY KEY AUTOINCREMENT, pattern_key TEXT NOT NULL UNIQUE, summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'candidate', confidence REAL NOT NULL DEFAULT 0, support_count INTEGER NOT NULL DEFAULT 0, avg_r_multiple REAL NOT NULL DEFAULT 0, win_rate_pct REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
```

## `kis_blocks`

Path: `.runtime/kis_blocks.db`

Tables:

- `blocks`: `block_id` PK, `symbol`, `name`, `qty_initial`, `qty_open`, `entry_price`, `target_price`, `stop_price`, thesis/reason/risk text, `created_by`, `manager_run_id`, `status`, `force_exit_requested`, `metadata_json`, created/updated/opened/closed timestamps.
- `block_orders`: order attempts by block with `symbol`, `side`, integer `qty`, KIS `limit_price`, `order_type`, `status`, `order_no`, `order_orgno`, filled/remaining quantities, avg fill price, cancel fields, response JSON, timestamps.
- `block_events`: append log keyed by autoincrement `id`, with `block_id`, `event_type`, `message`, `payload_json`, `created_at`.
- `manager_runs`: LLM manager run history with `market_session`, `status`, `mode`, `model`, error, prompt/response/actions JSON.
- `quote_snapshots`: per-symbol quote capture with price/source/status/error/raw JSON.
- `reconciliation_runs`: account and block reconciliation summaries.
- `system_state`: key/value JSON state store.

Important indexes: `blocks(status, symbol)`, `block_events(block_id, id DESC)`, `block_orders(block_id, id DESC)`, `quote_snapshots(symbol, fetched_at DESC)`.

## `binance_blocks`

Path: `.runtime/binance_blocks.db`

Tables:

- `blocks`: `block_id` PK, `symbol`, `market`, `side`, real-valued quantity fields, entry/target/stop prices, leverage/margin/liquidation fields, thesis/reason/risk text, manager/status/force-exit metadata, created/updated/opened/closed timestamps.
- `block_orders`: Binance order attempts with `symbol`, `market`, `side`, real `qty`, `order_type`, `status`, reason, response JSON, timestamps.
- `block_events`: append log with `block_id`, event type, message, payload JSON, created time.
- `block_performance_reflections`: one reflection per `block_id`, including symbol/market/side, entry/exit/stop/target, `pnl_usdt`, `r_multiple`, MFE/MAE R multiples, `pattern_key`, lesson JSON, created time.
- `kill_switch`: singleton row (`id = 1`) with enabled flag, reason, updated time.
- `manager_runs`: run history with status/mode/model/error and prompt/response/actions JSON.
- `quote_snapshots`: symbol/market price captures with source/status/error/raw JSON.

Important indexes: `blocks(status, market, symbol)`, `quote_snapshots(market, symbol, fetched_at DESC)`, `block_performance_reflections(created_at DESC)`, block event/order indexes by `block_id`.

## `investment_memory`

Path: `.runtime/investment_memory.db`

Tables:

- `block_reflections`: `block_id` PK, symbol/name/status/exit reason, `pnl_krw`, `pnl_pct`, MFE/MAE pct, hold seconds, rule-follow flag, lesson Markdown, metrics JSON, source run, timestamps.
- `memory_runs`: run history with kind/slot/status/mode/model/error and input/output JSON.
- `memory_events`: unique `event_key`, event type, block id, processing status, payload JSON, created/processed timestamps.
- `memory_insights`: typed memory records with key/status/confidence, summary Markdown, evidence JSON, source run.
- `daily_journals`: unique `(trading_day, slot)` journal messages, file path, context JSON, Telegram result fields.
- `symbol_analyses`: symbol analysis history with trigger/source/model/status, short/mid/long views, stance/confidence, reasons/risks/gaps/triggers/targets/stops JSON, snapshot/prompt/raw response/error, timestamps.
- `policy_scorecards`: latest per-policy sample/win/expectancy/rule-follow/confidence summary.
- `policy_rules`: versioned policy rules with unique `rule_id`, status/action, condition/effect/evidence/source scorecard JSON, file path, lifecycle timestamps.
- `policy_changes`: candidate policy changes with action/strength/status/reason/confidence/source run.
- `policy_revisions`: revision records with scope, condition/effect/evidence JSON, reason Markdown, confidence, lifecycle timestamps.
- `policy_outcomes`: composite PK `(policy_id, rule_id, period_key, period_type)`, sample metrics and helped/hurt counts.
- `period_reviews`: composite PK `(period_key, period_type)`, metrics JSON, review Markdown, linked revision ids.
- `telegram_sends`: trading day/slot send status and result JSON.

Important indexes: memory event status, memory insight lookup, memory runs by kind/slot/time, block reflections by symbol, symbol analyses by symbol or trigger, policy rules/revisions status.

## `market_judgment`

Path: `.runtime/market_judgment.db`

Tables:

- `judgment_runs`: LLM judgment run history with `market_session`, status, mode/model/query/error, prompt/response/source snapshot JSON.
- `symbol_judgments`: per-run symbol stance, account action, horizon/confidence, reasons/risks/triggers/gaps JSON, quote/position/strategy JSON.
- `quote_snapshots`: symbol quote history with price/change/OHLC/volume/trading value, source/status/error/raw JSON.
- `account_snapshots`: cash, position value, total value, position count, status/error/raw JSON.

Important indexes: `symbol_judgments(run_id, symbol)`, `quote_snapshots(symbol, fetched_at DESC)`.

## `market_pulse`

Path: `.runtime/market_pulse.db`

Tables:

- `market_pulse_snapshots`: captured/trading day/status, `regime`, score, indices JSON, sector JSON, block alignment JSON, risk flags JSON, data gaps JSON, raw JSON.

Important index: `market_pulse_snapshots(captured_at DESC)`.

## `naver_reports`

Path: `.runtime/naver_reports.db`

Tables:

- `reports`: broker report metadata with `doc_id`, category/source/detail/PDF URLs, title/company/broker/analyst/symbol, published/crawled times, PDF hash/archive path, content source/body, timestamps.
- `report_chunks`: report chunk text with page/section metadata.
- `report_facts`: extracted rating, target price/currency/change flag, valuation method/value/basis/notes, summary/thesis/risks/earnings/catalysts/evidence JSON.
- `report_symbol_links`: composite PK `(report_id, symbol, link_type)`, linked symbol/name/asset class/source/confidence/evidence, timestamps.
- `symbol_directory`: `symbol` PK, company name, market/status/source/confidence, first seen/updated/verified timestamps.

Important indexes: reports by analyst/broker/category/symbol/date, report `doc_id`, symbol links by report and by symbol/asset class/confidence, symbol directory by company name.

## `symbol_fundamentals`

Path: `.runtime/symbol_fundamentals.db`

Tables:

- `valuation_snapshots`: `snapshot_id` PK, symbol/name, price, market cap KRW, PER/EPS/PBR/BPS/dividend yield, industry PER/name, source/as-of/raw JSON, crawl/status/error/attempt timestamps.
- `financial_snapshots`: `financial_id` PK, symbol, period type/period, revenue, operating profit, net income, ROE, debt ratio, operating margin, raw JSON, crawled time.
- `valuation_scores`: `symbol` PK, under/over valuation scores, quality/growth scores, relative discount, PBR/ROE fit, label, reasons/risks JSON, scored time.

Important indexes: valuation snapshots by `symbol, crawled_at DESC` and `status, crawled_at DESC`.

## `etf_research`

Path: `.runtime/etf_research.db`

Tables:

- `etf_universe`: `symbol` PK, name, category, tags JSON, updated time.
- `etf_market_snapshots`: market price/change/volume/turnover/source/raw JSON/status/error captured per symbol.
- `etf_scores`: scored ETF rows with label, liquidity/momentum/core-fit/risk scores, reasons/risks JSON, scored time.

Important indexes: market snapshots by `symbol, captured_at DESC, id DESC`; scores by `symbol, scored_at DESC, id DESC`.

## `llm_usage`

Path: `.runtime/llm_usage.db`

Tables:

- `llm_calls`: call history with start/finish/trading day, component, operation, model, mode, status, latency, prompt/completion/total tokens, usage source, input/output chars, error, metadata JSON.

Important indexes: calls by trading day/component/time, model/trading day, status/trading day.

## `crypto_market_research`

Path: `.runtime/crypto_market_research.db`

Tables:

- `crypto_symbols`: `symbol` PK, base/quote assets, spot/futures enabled flags, liquidity tier, updated time.
- `crypto_klines`: composite PK `(symbol, market, interval, open_time)`, OHLCV, quote volume, close time, raw JSON.
- `crypto_market_snapshots`: symbol/market price, USDT quote volume, 24h change, spread bps, raw JSON, captured time.
- `crypto_derivatives`: mark/index price, funding rate, next funding time, open interest, long/short ratio, raw JSON, captured time.
- `crypto_features`: latest feature JSON, score, regime, updated time per symbol.
- `crypto_candidates`: latest candidate per symbol with market/stance/horizon/score/confidence, reason Markdown, block template JSON, source run, updated time.
- `crypto_symbol_notes`: latest notes per symbol with stance/horizon/confidence, summary Markdown, reasons/risks/triggers JSON.
- `crypto_research_runs`: run history with status/mode/model/prompt/response/error.
- `crypto_regime_snapshots`: regime payload history.
- `crypto_external_context`: composite PK `(source_id, key)`, payload JSON and capture time.

Important indexes: market snapshots by symbol/market/time, derivatives by symbol/time, regime snapshots by captured time.

## `crypto_quant`

Path: `.runtime/crypto_quant.db`

Tables:

- `crypto_quant_signals`: composite PK `(symbol, horizon)`, current long/short/no-trade scores, expected R values, signal JSON, updated time.
- `crypto_quant_signal_history`: historical long/short/no-trade scores, expected R values, bias, signal JSON, captured time.
- `crypto_quant_outcomes`: labeled outcomes with symbol/side/horizon/source, outcome, R/MFE/MAE metrics, payload JSON, labeled time.

Important indexes: current signals by updated time, signal history by symbol/horizon/time, outcomes by symbol/labeled time.

## `crypto_pattern_lab`

Path: `.runtime/crypto_pattern_lab.db`

Tables:

- `freqtrade_strategy_sources`: `source_id` PK, path, strategy name, source hash, import status/error/time.
- `strategy_patterns`: `pattern_id` PK, source id, name, family, direction, timeframe, indicators/expression/risk tags JSON, created time.
- `freqtrade_ohlcv_imports`: imported OHLCV file path, symbol, interval, row count, status/error/import time.
- `pattern_backtests`: backtest metrics by pattern/symbol/interval/sample window with trade count, win rate, expectancy R, avg R, profit factor, max loss, MFE/MAE R, regime, score, warnings JSON, evaluated time.
- `optimization_runs`: one bounded optimizer run per pattern/symbol/interval, including search space, objective, trial count, best trial, sample window, and status.
- `optimization_trials`: parameter-set trial metrics for stop/target/holding-bar combinations.
- `optimized_strategy_sets`: active promoted parameter sets exposed to Binance Jue as audited price-geometry priors.

Important indexes: strategy patterns by family/direction/timeframe, backtests by symbol/interval/evaluated time, optimization trials by run, and optimized sets by symbol/interval/objective score.

## `crypto_alpha`

Path: `.runtime/crypto_alpha.db`

Tables:

- `crypto_alpha_sources`: `source_id` PK, label, URL, source type, trust score, enabled flag, last crawl/status/error, updated time.
- `crypto_alpha_snapshots`: source snapshots with unique `(source_id, content_hash)`, URL/title/raw text/summary/raw JSON, crawled/status/error fields.
- `crypto_alpha_events`: detected events with snapshot/source/type/title/summary/event time/detected time/confidence/importance/decay/status/raw JSON.
- `crypto_alpha_event_symbols`: composite PK `(event_id, symbol)`, base asset, link confidence, impact direction/horizon, reason.
- `crypto_alpha_event_outcomes`: composite PK `(event_id, symbol, horizon)`, return/MFE/MAE pct, R multiple, regime, measured time.
- `crypto_alpha_hypotheses`: hypothesis id PK, unique `pattern_key`, summary/status/confidence/support count/avg R/win rate/updated time.
- `crypto_alpha_context_cache`: `cache_key` PK, payload JSON, created time.

Important indexes: snapshots by source/time and unique source/content hash, events by type/time, event symbols by symbol/event id.
