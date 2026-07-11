# Config & Environment

## Source

Settings are defined in `src/tradecraft/config.py` using `AppSettings`.

`AppSettings` uses `pydantic_settings.BaseSettings` with `env_file=".env"` and `extra="ignore"`. Field aliases and validation aliases are part of the API: preserve them when refactoring because tests and local operator config may depend on both current and legacy env names.

Captured setting names from `AppSettings.model_fields` on 2026-07-12: 510 fields.

## Critical Setting Families

| Family | Examples | Notes |
| --- | --- | --- |
| Admin/Auth | `admin_token`, `admin_tokens`, env aliases `TRADECRAFT_ADMIN_TOKEN`, `TRADECRAFT_ADMIN_TOKENS` | Required for protected operational APIs. `admin_token_list` includes comma-separated and legacy token values. |
| Telegram | `telegram_bot_token`, `telegram_chat_id`, `telegram_webhook_secret` | Operator messages and webhook command guards; `telegram_*` also has legacy validation aliases for token/chat ID. |
| LLM | `codex_runtime_mode_preference`, `codex_runtime_sdk_codex_bin`, `codex_runtime_timeout_ms`, `llm_model`, `llm_reasoning_effort`, `llm_reasoning_model`, `llm_reasoning_model_effort`, `llm_utility_model`, `llm_utility_model_effort`, `llm_offline_model`, `llm_offline_model_effort`, `llm_usage_enabled`, `llm_usage_db_path` | Routes critical trading to Sol/xhigh, analysis and memory to Terra/high, extraction to Luna/medium, and offline policy revision to Sol/max while preserving usage telemetry. |
| KIS | `kis_base_url`, `kis_primary_app_key`, `kis_primary_app_secret`, `kis_primary_account_no`, `kis_primary_product_code`, `kis_secondary_*`, `kis_rate_limit_enabled`, `kis_rest_rate_limit_per_sec`, `kis_token_min_interval_sec`, `kis_rate_limit_db_path`, `dashboard_kis_balance_cache_ttl_sec`, `dashboard_kis_balance_error_cooldown_sec` | Enables Korean account, quotes, orders, shared token/rate limiting, and dashboard balance cache/error backoff. |
| KIS Block Trader | `kis_block_trader_enabled`, `kis_block_trader_once`, `kis_block_trader_execute_orders`, `kis_block_trader_db_path`, `kis_block_trader_state_path`, `kis_block_trader_rule_interval_sec`, `kis_block_trader_manager_interval_sec`, `kis_pre_open_monitor_enabled`, `kis_pre_open_monitor_symbols`, `kis_pre_open_monitor_max_symbols`, `kis_pre_open_monitor_interval_sec`, `kis_block_trader_aggressive_limit_bps`, `kis_block_trader_pending_reconcile_timeout_sec`, `kis_block_trader_failed_exit_retry_cooldown_sec`, `kis_block_trader_max_manager_symbols`, `kis_block_trader_manager_query`, `block_horizon_targets`, `kis_block_trader_etf_universe`, `kis_block_trader_quote_retention_days`, `kis_block_trader_reconciliation_retention_days`, `kis_block_trader_manager_run_retention_days`, `kis_block_trader_archive_retention_days` | Active KIS 쥬 block-trading loop, pre-open monitoring, and deterministic executor configuration. |
| Binance Credentials | `binance_spot_api_key`, `binance_spot_api_secret`, `binance_spot_base_url`, `binance_futures_api_key`, `binance_futures_api_secret`, `binance_futures_base_url`, `binance_usdt_krw` | Enables crypto spot/futures adapters and KRW conversion. Futures keys fall back to spot keys where configured in properties. |
| Binance Block Trader | `binance_block_trader_enabled`, `binance_block_trader_execute_spot_orders`, `binance_block_trader_execute_futures_orders`, `binance_block_trader_once`, `binance_block_trader_db_path`, `binance_block_trader_state_path`, `binance_block_trader_quote_interval_sec`, `binance_block_trader_rule_interval_sec`, `binance_block_trader_manager_interval_sec`, `binance_block_trader_waiting_entry_max_age_sec`, `binance_block_trader_entry_pending_max_age_sec`, `binance_block_trader_llm_model`, `binance_block_trader_llm_reasoning_effort`, `binance_block_trader_max_manager_symbols`, `binance_block_trader_spot_universe`, `binance_block_trader_futures_universe`, `binance_block_trader_max_futures_leverage`, `binance_block_trader_min_liquidation_distance_pct`, `binance_block_trader_aggressive_limit_bps`, `binance_block_trader_account_risk_pct`, `binance_block_trader_max_total_exposure_usdt`, `binance_block_trader_max_symbol_exposure_pct`, `binance_block_trader_min_reward_risk`, `binance_block_trader_quote_retention_days`, `binance_block_trader_manager_run_retention_days`, `binance_block_trader_archive_retention_days` | Active Binance 쥬 spot/futures block-trading, risk, retention, and manager behavior. |
| Crypto Intelligence | `crypto_market_research_*`, `crypto_quant_*`, `crypto_pattern_lab_*`, `crypto_alpha_*` | Binance-specific market research, quant features, pattern lab, and alpha/outcome memory. |
| Memory | `investment_memory_enabled`, `investment_memory_once`, `investment_memory_root_path`, `investment_memory_db_path`, `investment_memory_state_path`, `investment_memory_poll_interval_sec`, `investment_memory_send_telegram`, `investment_memory_policy_mode`, `investment_memory_persona_tone`, `investment_memory_context_max_chars`, `investment_memory_validation_event_retained_rows_per_venue`, `investment_memory_run_recent_rows_per_group`, `investment_memory_symbol_analysis_recent_rows_per_symbol` | 쥬 growth loop, rituals, reflections, policy revisions, and memory Markdown/SQLite paths. Runtime compaction keeps recent validation event provenance and recent detailed prompt snapshots while policy scorecards/insights preserve the learned signal. |
| Jue Wiki | `jue_wiki_enabled`, `jue_wiki_root_path`, `jue_wiki_db_path`, `jue_wiki_shadow_db_path`, `jue_wiki_provenance_key_path`, `jue_wiki_context_max_chars`, `jue_wiki_runner_interval_sec`, `jue_wiki_repair_overdue_sec`, `jue_wiki_repair_stall_sec`, `jue_wiki_repair_growth_window_sec`, `jue_wiki_repair_growth_warn_count`, `jue_wiki_page_max_chars`, `jue_wiki_context_page_limit`, `jue_wiki_prompt_mode`, `jue_wiki_read_mode`, `jue_wiki_promotion_thresholds_json`, `jue_wiki_selector_max_pages`, `jue_wiki_selector_min_confidence`, `jue_wiki_exclude_lint_warnings`, `jue_wiki_repair_enabled`, `jue_wiki_full_prompt_max_chars`, `jue_wiki_application_enabled`, `jue_wiki_effectiveness_weight`, `jue_wiki_effectiveness_max_adjustment`, `jue_wiki_effectiveness_min_samples`, `jue_wiki_mode_recommendation_min_samples` | Compiled Markdown knowledge layer, selector, read-policy gate, progress/deadline-based repair health, playbook metrics, applied-intelligence outcome feedback, and prompt integration above RAG, block ledgers, and memory. |
| Live Evaluation | `live_evaluator_enabled`, `live_evaluator_once`, `live_evaluator_db_path`, `live_performance_db_path`, `live_evaluator_state_path`, `live_evaluator_interval_sec`, `live_authority_max_scale_multiplier`, `live_authority_min_samples_to_scale` | Live performance/edge evaluator, authority packet DB/state paths, and scale-up caps. |
| Market | `market_judge_enabled`, `market_judge_once`, `market_judge_db_path`, `market_judge_state_path`, `market_quote_interval_sec`, `market_judge_interval_sec`, `market_judge_max_symbols`, `market_judge_llm_max_symbols`, `market_judge_prompt_target_chars`, `market_judge_prompt_warn_chars`, `market_judge_prompt_max_chars`, `market_judge_use_naver_fallback`, `market_judge_query`, `market_pulse_*` | KRX market judgment, typed prompt budgets, and market pulse context for active block operation. |
| Research/Reports/RAG | `research_*`, `naver_reports_*` including `naver_reports_state_path`, `naver_reports_heartbeat_interval_sec`, and `naver_reports_worker_terminate_grace_sec`, `rag_*`, `valuation_*`, `etf_research_*`, `daily_discovery_*`, `strategy_insight_*`, `market_intelligence_sources_json`, `strategy_insight_sources_json` | Research DBs, supervised Naver report collection, RAG, valuation, ETF, daily discovery, Whale/세시반, and strategy candidate sources. |
| Runtime/Storage | `runtime_state_path`, `runtime_sessions_path`, `ops_readiness_snapshot_path`, `ops_readiness_refresh_interval_sec`, `ops_readiness_snapshot_max_age_sec`, `runtime_max_age_sec`, `runtime_write_interval_sec`, `runtime_storage_large_file_threshold_mb`, `runtime_storage_prune_unreferenced_pdfs`, `runtime_storage_prune_extracted_report_pdfs`, `runtime_storage_extracted_report_pdf_retention_days`, `runtime_storage_prune_old_runtime_logs`, `runtime_storage_log_retention_days`, `runtime_storage_prune_scratch_artifacts`, `runtime_storage_scratch_artifact_retention_days`, `host`, `port`, `allow_origins` | Main runtime state, read-only readiness snapshot publication, process/session freshness, storage cleanup policy, server binding, and CORS. |
| Reports API Microservice | `reports_api_host`, `reports_api_port`, `reports_api_token`, `reports_api_tokens`, `reports_ui_allowed_cidrs`, `reports_ui_trust_proxy`, `reports_worker_state_path` | Separate reports API/UI worker surface and auth. |

## Real Trading Toggles

Exact current setting names from `src/tradecraft/config.py`:

| Setting | Meaning |
| --- | --- |
| `kis_block_trader_enabled` | Enables the KIS block trader loop. |
| `kis_block_trader_execute_orders` | Allows KIS block executor/manager paths to send real KIS orders. This is a hard active-trading toggle. |
| `binance_block_trader_enabled` | Enables the Binance block trader loop. |
| `binance_block_trader_execute_spot_orders` | Allows Binance spot block orders. |
| `binance_block_trader_execute_futures_orders` | Allows Binance futures block orders. |
| `portfolio_coach_review_queue_enabled` | Keeps portfolio coach output operator-reviewed before action. |
| `rag_allow_legacy_pickle_migration` | Allows explicitly gated legacy pickle/RAG migration; keep disabled unless migrating intentionally. |

Readiness and UI settings surfaces must keep live/paper state visible. A disabled execute flag means 쥬 may still analyze and propose, but deterministic executors must not place real orders.

## Crypto Research Retention Settings

The Binance/crypto research runner watches a large universe, so raw market
snapshots and quant signal history must be bounded separately from durable
learning artifacts. Outcomes, scorecards, pattern sets, block history, and wiki
memory carry long-term learning; raw candles and per-cycle signal JSON are
regenerable operating data.

| Setting | Env Alias | Meaning |
| --- | --- | --- |
| `crypto_market_research_retention_days` | `TRADECRAFT_CRYPTO_MARKET_RESEARCH_RETENTION_DAYS` | Hot raw market snapshots, derivatives, regimes, and klines kept in active tables, default `3`. |
| `crypto_market_research_archive_retention_days` | `TRADECRAFT_CRYPTO_MARKET_RESEARCH_ARCHIVE_RETENTION_DAYS` | Total warm archive horizon for compressed crypto research raw rows, default `7`. Rows colder than this are deleted directly. |
| `crypto_market_research_kline_hot_window_rows` | `TRADECRAFT_CRYPTO_MARKET_RESEARCH_KLINE_HOT_WINDOW_ROWS` | Extra hot-table cap for candles per `symbol + market + interval`, default `720`. Older hot candles outside the window are deleted because they are regenerable operating data. |
| `crypto_market_research_market_hot_window_rows` | `TRADECRAFT_CRYPTO_MARKET_RESEARCH_MARKET_HOT_WINDOW_ROWS` | Extra hot-table cap for market snapshots per `symbol + market` and derivatives per `symbol`, default `720`. |
| `crypto_quant_retention_days` | `TRADECRAFT_CRYPTO_QUANT_RETENTION_DAYS` | Hot quant signal history/outcomes kept in active tables, default `3`. |
| `crypto_quant_archive_retention_days` | `TRADECRAFT_CRYPTO_QUANT_ARCHIVE_RETENTION_DAYS` | Total warm archive horizon for compressed quant signal history/outcomes, default `7`. Rows colder than this are deleted directly. |
| `crypto_quant_hot_window_rows` | `TRADECRAFT_CRYPTO_QUANT_HOT_WINDOW_ROWS` | Extra hot-table cap for quant signal history per `symbol + horizon`, default `360`. |
| `etf_research_retention_days` | `TRADECRAFT_ETF_RESEARCH_RETENTION_DAYS` | Hot ETF quote/snapshot rows kept in active tables, default `3`. |
| `etf_research_archive_retention_days` | `TRADECRAFT_ETF_RESEARCH_ARCHIVE_RETENTION_DAYS` | Total warm archive horizon for compressed ETF snapshot rows, default `7`. Rows colder than this are deleted directly. |

The retention pruner archives compressed rows in batches to avoid loading
millions of rows into memory during cleanup.

## Live Evaluation Settings

Exact current live evaluator aliases:

| Setting | Env Alias | Meaning |
| --- | --- | --- |
| `live_evaluator_enabled` | `TRADECRAFT_LIVE_EVALUATOR_ENABLED` | Enables the evaluator runner and readiness expectation. |
| `live_evaluator_once` | `TRADECRAFT_LIVE_EVALUATOR_ONCE` | Runs a single evaluator cycle and exits. |
| `live_evaluator_db_path` | `TRADECRAFT_LIVE_EVALUATOR_DB_PATH` | Path to the live edge DB, default `.runtime/live_edge.db`. |
| `live_performance_db_path` | `TRADECRAFT_LIVE_PERFORMANCE_DB_PATH` | Path to the live performance DB, default `.runtime/live_performance.db`. |
| `live_evaluator_state_path` | `TRADECRAFT_LIVE_EVALUATOR_STATE_PATH` | Runtime JSON state path, default `.runtime/live_evaluator.json`. |
| `live_evaluator_interval_sec` | `TRADECRAFT_LIVE_EVALUATOR_INTERVAL_SEC` | Evaluator loop cadence, default `300`, with a 30-second runner minimum. |
| `trading_validation_payload_compaction_enabled` | `TRADECRAFT_TRADING_VALIDATION_PAYLOAD_COMPACTION_ENABLED` | Compacts old detailed validation payloads after each evaluator cycle, default `true`. |
| `trading_validation_payload_recent_rows_per_group` | `TRADECRAFT_TRADING_VALIDATION_PAYLOAD_RECENT_ROWS_PER_GROUP` | Detailed validation rows to keep per venue/scope/revision group, default `48`. |
| `trading_validation_payload_max_rows_per_group` | `TRADECRAFT_TRADING_VALIDATION_PAYLOAD_MAX_ROWS_PER_GROUP` | Maximum validation history rows retained per venue/scope/revision group after compaction, default `720`. |
| `trading_validation_payload_compact_min_chars` | `TRADECRAFT_TRADING_VALIDATION_PAYLOAD_COMPACT_MIN_CHARS` | Minimum payload size before old validation rows are compacted, default `20000`. |
| `live_authority_max_scale_multiplier` | `TRADECRAFT_LIVE_AUTHORITY_MAX_SCALE_MULTIPLIER` | Upper cap for authority packet scaling, default `1.5`. |
| `live_authority_min_samples_to_scale` | `TRADECRAFT_LIVE_AUTHORITY_MIN_SAMPLES_TO_SCALE` | Minimum sample count before scale-up authority can exceed `1.0`, default `10`. |

These settings affect evidence-backed manager authority, not exchange access by
themselves. Real orders still require the venue execution toggles and all hard
readiness gates.

## Jue Wiki Settings

Exact current Jue Wiki aliases:

| Setting | Env Alias | Meaning |
| --- | --- | --- |
| `jue_wiki_enabled` | `TRADECRAFT_JUE_WIKI_ENABLED` | Enables the compiled wiki layer and context provider, default `true`. |
| `jue_wiki_root_path` | `TRADECRAFT_JUE_WIKI_ROOT_PATH` | Markdown wiki root, default `.runtime/jue_wiki`. |
| `jue_wiki_db_path` | `TRADECRAFT_JUE_WIKI_DB_PATH` | SQLite provenance index, default `.runtime/jue_wiki/wiki.db`. |
| `jue_wiki_shadow_db_path` | `TRADECRAFT_JUE_WIKI_SHADOW_DB_PATH` | Persistent shadow recordings, comparisons, and eligibility rollups outside live `.runtime`, default `~/.tradecraft/jue_wiki_shadow.db` (normalized to an absolute path). |
| `jue_wiki_provenance_key_path` | `TRADECRAFT_JUE_WIKI_PROVENANCE_KEY_PATH` | Local 0600 HMAC-SHA256 key used to sign and verify replay completion provenance, default `~/.tradecraft/jue_wiki_provenance.key`. |
| `jue_wiki_context_max_chars` | `TRADECRAFT_JUE_WIKI_CONTEXT_MAX_CHARS` | Maximum compact wiki context size for manager prompts, default `24000`. |
| `jue_wiki_page_max_chars` | `TRADECRAFT_JUE_WIKI_PAGE_MAX_CHARS` | Maximum compiled Markdown page size, default `12000`. |
| `jue_wiki_context_page_limit` | `TRADECRAFT_JUE_WIKI_CONTEXT_PAGE_LIMIT` | Maximum page count included in a context pack, default `8`. |
| `jue_wiki_runner_interval_sec` | `TRADECRAFT_JUE_WIKI_RUNNER_INTERVAL_SEC` | Periodic rebuild/lint cadence, default `1800`, with a 300-second runner minimum. |
| `jue_wiki_repair_overdue_sec` | `TRADECRAFT_JUE_WIKI_REPAIR_OVERDUE_SEC` | Oldest open repair age considered overdue after progress stalls, default `86400`. |
| `jue_wiki_repair_stall_sec` | `TRADECRAFT_JUE_WIKI_REPAIR_STALL_SEC` | Maximum age of the latest resolved repair before an open queue is stalled, default `21600`. |
| `jue_wiki_repair_growth_window_sec` | `TRADECRAFT_JUE_WIKI_REPAIR_GROWTH_WINDOW_SEC` | Window used to compare created and resolved repair counts, default `86400`. |
| `jue_wiki_repair_growth_warn_count` | `TRADECRAFT_JUE_WIKI_REPAIR_GROWTH_WARN_COUNT` | Net new repairs in the growth window that trigger an operational warning, default `25`. |
| `jue_wiki_prompt_mode` | `TRADECRAFT_JUE_WIKI_PROMPT_MODE` | Manager prompt integration mode: `observe`, `assist`, or `primary`; default `assist`. |
| `jue_wiki_read_mode` | `TRADECRAFT_JUE_WIKI_READ_MODE` | Independent Wiki read enforcement mode: `shadow`, `prefer`, or `required`; default `shadow`. `required` suppresses only new-risk actions when the decision gate is closed and preserves exits, reductions, reconciliation, and kill-switch behavior. |
| `jue_wiki_promotion_thresholds_json` | `TRADECRAFT_JUE_WIKI_PROMOTION_THRESHOLDS_JSON` | Optional venue-to-playbook positive integer sample thresholds, for example `{"kis":{"swing":30},"binance":{"intraday":50}}`; default `{}` disables automatic promotion. Malformed, boolean, zero, or negative thresholds also disable promotion and surface deterministic configuration warnings. |
| `jue_wiki_selector_max_pages` | `TRADECRAFT_JUE_WIKI_SELECTOR_MAX_PAGES` | Maximum selected pages for a Phase 2 selector request, default `12`. |
| `jue_wiki_selector_min_confidence` | `TRADECRAFT_JUE_WIKI_SELECTOR_MIN_CONFIDENCE` | Minimum wiki page confidence allowed into selector candidates, default `0.15`. |
| `jue_wiki_exclude_lint_warnings` | `TRADECRAFT_JUE_WIKI_EXCLUDE_LINT_WARNINGS` | When true, selector excludes pages with open lint findings instead of only penalizing/reporting them; default `false`. |
| `jue_wiki_repair_enabled` | `TRADECRAFT_JUE_WIKI_REPAIR_ENABLED` | Enables the runner/API repair pass that converts open lint findings into repair actions, default `true`. |
| `jue_wiki_full_prompt_max_chars` | `TRADECRAFT_JUE_WIKI_FULL_PROMPT_MAX_CHARS` | Upper prompt-budget cap for selected wiki context in full manager prompt assembly, default `190000`. |
| `jue_wiki_application_enabled` | `TRADECRAFT_JUE_WIKI_APPLICATION_ENABLED` | Enables Phase 3 Applied Intelligence projection in the wiki runner, default `true`. |
| `jue_wiki_effectiveness_weight` | `TRADECRAFT_JUE_WIKI_EFFECTIVENESS_WEIGHT` | Bounded selector weighting multiplier for page helpful scores, default `0.12`. |
| `jue_wiki_effectiveness_max_adjustment` | `TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MAX_ADJUSTMENT` | Absolute cap for effectiveness selector adjustment, default `8.0`. |
| `jue_wiki_effectiveness_min_samples` | `TRADECRAFT_JUE_WIKI_EFFECTIVENESS_MIN_SAMPLES` | Minimum sample count before a page can leave probe status, default `5`. |
| `jue_wiki_mode_recommendation_min_samples` | `TRADECRAFT_JUE_WIKI_MODE_RECOMMENDATION_MIN_SAMPLES` | Minimum aggregate samples before mode recommendations can move beyond observe, default `20`. |

These settings configure compiled context only. Source-of-truth remains reports/RAG stores, KIS/Binance block ledgers, and `investment_memory.db`; wiki pages must retain source references for audit.

Prompt mode semantics:

- `observe`: selector runs and records selection traces, but manager prompts keep
  their existing context structure.
- `assist`: selected wiki pages and budget reports are included with normal raw
  context caps.
- `primary`: selected wiki pages become the main compiled knowledge packet, and
  raw reports/RAG/memory are kept as bounded source evidence.

The source-of-truth boundary does not change with these settings. Wiki is a
compiled interpretation layer; reports/RAG, block ledgers, live performance, and
the memory DB remain authoritative.

## Captured Settings Names

The full captured field list is kept here so refactors can compare names before changing env aliases or settings-catalog mappings.

```text
telegram_bot_token
telegram_chat_id
telegram_webhook_secret
admin_token
admin_tokens
upbit_access_key
upbit_secret_key
upbit_base_url
bithumb_access_key
bithumb_secret_key
bithumb_base_url
binance_spot_api_key
binance_spot_api_secret
binance_spot_base_url
binance_futures_api_key
binance_futures_api_secret
binance_futures_base_url
binance_usdt_krw
usd_krw
fx_cache_ttl_sec
dashboard_kis_balance_cache_ttl_sec
dashboard_crypto_balance_cache_ttl_sec
dashboard_stale_balance_cache_ttl_sec
dashboard_kis_balance_error_cooldown_sec
dashboard_balance_fetch_timeout_sec
dashboard_kis_us_balance_enabled
dashboard_payload_disk_cache_enabled
dashboard_payload_cache_path
kis_base_url
kis_primary_app_key
kis_primary_app_secret
kis_primary_account_no
kis_primary_product_code
kis_secondary_app_key
kis_secondary_app_secret
kis_secondary_account_no
kis_secondary_product_code
kis_rate_limit_enabled
kis_rest_rate_limit_per_sec
kis_account_min_interval_sec
kis_token_min_interval_sec
kis_rate_limit_db_path
runtime_state_path
runtime_sessions_path
ops_readiness_snapshot_path
ops_readiness_refresh_interval_sec
ops_readiness_snapshot_max_age_sec
backtest_cycles
backtest_step_sec
backtest_speed
backtest_initial_price
backtest_volatility_bps
backtest_drift_bps
backtest_fee_rate
backtest_slippage_bps
backtest_seed
backtest_state_path
backtest_result_path
backtest_data_registry_path
backtest_max_curve_points
backtest_emit_interval
runtime_max_age_sec
runtime_write_interval_sec
runtime_cold_archive_root
runtime_storage_archive_dryrun
runtime_storage_dryrun_hot_hours
runtime_storage_dryrun_hot_per_scenario
runtime_storage_archive_rag_rebuild_backups
runtime_storage_large_file_threshold_mb
runtime_storage_prune_unreferenced_pdfs
runtime_storage_prune_extracted_report_pdfs
runtime_storage_extracted_report_pdf_retention_days
runtime_storage_prune_rag_repair_artifacts
runtime_storage_rag_repair_artifact_retention_days
runtime_storage_prune_rag_rebuild_backups
runtime_storage_rag_rebuild_backup_retention_days
runtime_storage_prune_old_runtime_logs
runtime_storage_log_retention_days
runtime_storage_rotate_large_active_logs
runtime_storage_active_log_max_mb
runtime_storage_active_log_tail_kb
runtime_storage_prune_scratch_artifacts
runtime_storage_scratch_artifact_retention_days
runtime_storage_prune_old_backtest_artifacts
runtime_storage_backtest_artifact_retention_days
runtime_storage_prune_old_ui_check_artifacts
runtime_storage_ui_check_artifact_retention_days
runtime_storage_prune_zero_byte_runtime_markers
runtime_storage_zero_byte_marker_retention_days
runtime_storage_database_compact_min_free_mb
runtime_storage_database_compact_min_free_ratio_pct
research_state_path
research_max_age_sec
research_enabled
research_runner_collect_reports
research_run_interval_sec
intelligence_once
research_max_items
research_knowledge_max_chars
research_advice_context_max_chars
research_db_reference_top_k
research_market_scope
research_codex_command
research_codex_query
research_codex_timeout_sec
codex_runtime_mode_preference
codex_runtime_sdk_codex_bin
codex_runtime_timeout_ms
codex_native_thread_mode
codex_native_thread_db_path
codex_native_compact_after_turns
codex_native_read_turns
codex_native_account_check_interval_sec
codex_native_model_check_interval_sec
codex_native_developer_instructions_enabled
jue_codex_lab_enabled
jue_codex_lab_interval_sec
jue_codex_lab_autonomy_mode
jue_codex_lab_db_path
jue_codex_lab_max_patch_bytes
jue_codex_lab_allowed_paths
jue_codex_lab_blocked_paths
jue_codex_lab_max_tasks_per_cycle
jue_codex_lab_market_hours_hot_deploy
llm_model
llm_reasoning_effort
jue_strategy_revision_id
llm_usage_enabled
llm_usage_db_path
jue_wiki_enabled
jue_wiki_root_path
jue_wiki_db_path
jue_wiki_shadow_db_path
jue_wiki_provenance_key_path
jue_wiki_context_max_chars
jue_wiki_runner_interval_sec
jue_wiki_repair_overdue_sec
jue_wiki_repair_stall_sec
jue_wiki_repair_growth_window_sec
jue_wiki_repair_growth_warn_count
jue_wiki_page_max_chars
jue_wiki_context_page_limit
jue_wiki_prompt_mode
jue_wiki_read_mode
jue_wiki_promotion_thresholds_json
jue_wiki_selector_max_pages
jue_wiki_selector_min_confidence
jue_wiki_exclude_lint_warnings
jue_wiki_repair_enabled
jue_wiki_full_prompt_max_chars
jue_wiki_application_enabled
jue_wiki_effectiveness_weight
jue_wiki_effectiveness_max_adjustment
jue_wiki_effectiveness_min_samples
jue_wiki_mode_recommendation_min_samples
research_report_urls
research_strategy_md_path
portfolio_coach_enabled
portfolio_coach_user_id
portfolio_coach_db_path
portfolio_coach_lookback_days
portfolio_coach_concentration_threshold
portfolio_coach_max_candidates
portfolio_coach_top_n
portfolio_coach_option_count
portfolio_coach_trigger_count
portfolio_coach_time_horizon
portfolio_coach_max_single_position_weight
portfolio_coach_max_sector_weight
portfolio_coach_rebalance_frequency
portfolio_coach_risk_budget
portfolio_coach_idea_filters
portfolio_coach_factor_weights_json
portfolio_coach_ticker_name_map_json
portfolio_coach_review_queue_enabled
kis_block_trader_enabled
kis_block_trader_once
kis_block_trader_execute_orders
kis_block_trader_db_path
kis_block_trader_state_path
kis_block_trader_rule_interval_sec
kis_block_trader_manager_interval_sec
kis_block_trader_manager_error_retry_sec
kis_pre_open_monitor_enabled
kis_pre_open_monitor_symbols
kis_pre_open_monitor_max_symbols
kis_pre_open_monitor_interval_sec
kis_block_trader_retention_interval_sec
kis_block_trader_quote_retention_days
kis_block_trader_reconciliation_retention_days
kis_block_trader_manager_run_retention_days
kis_block_trader_archive_retention_days
kis_block_trader_aggressive_limit_bps
kis_block_trader_pending_reconcile_timeout_sec
kis_block_trader_failed_exit_retry_cooldown_sec
kis_block_trader_max_manager_symbols
kis_block_trader_prompt_target_chars
kis_block_trader_prompt_warn_chars
kis_block_trader_prompt_max_chars
kis_block_trader_manager_query
block_horizon_targets
kis_block_trader_etf_universe
binance_block_trader_enabled
binance_block_trader_execute_spot_orders
binance_block_trader_execute_futures_orders
binance_block_trader_execute_upbit_orders
binance_block_trader_once
binance_block_trader_db_path
binance_block_trader_state_path
binance_block_trader_quote_interval_sec
binance_block_trader_rule_interval_sec
binance_block_trader_manager_interval_sec
binance_block_trader_waiting_entry_max_age_sec
binance_block_trader_entry_pending_max_age_sec
binance_block_trader_manager_error_retry_sec
binance_block_trader_performance_feedback_interval_sec
binance_block_trader_telegram_reports_enabled
binance_block_trader_telegram_report_slots
binance_block_trader_llm_model
binance_block_trader_llm_reasoning_effort
binance_block_trader_llm_timeout_ms
binance_block_trader_max_manager_symbols
binance_block_trader_prompt_target_chars
binance_block_trader_prompt_warn_chars
binance_block_trader_prompt_max_chars
binance_block_trader_jue_wiki_context_max_chars
binance_block_trader_spot_universe
binance_block_trader_futures_universe
binance_block_trader_upbit_universe
binance_block_trader_max_futures_leverage
binance_block_trader_min_liquidation_distance_pct
binance_block_trader_aggressive_limit_bps
binance_block_trader_failed_exit_retry_cooldown_sec
binance_block_trader_min_entry_confidence
binance_block_trader_min_entry_expected_r
binance_block_trader_min_entry_directional_score
binance_block_trader_min_candidate_stop_pct
binance_block_trader_profit_lock_trigger_r
binance_block_trader_weak_lane_profit_lock_trigger_r
binance_block_trader_distressed_lane_profit_lock_trigger_r
binance_block_trader_entry_quality_loss_tighten_trigger_r
binance_block_trader_distressed_lane_min_samples
binance_block_trader_distressed_lane_max_win_rate_pct
binance_block_trader_distressed_lane_max_profit_factor
binance_block_trader_distressed_entry_quality_partial_profit_fraction
binance_block_trader_profit_lock_stop_r
binance_block_trader_profit_lock_min_net_buffer_pct
binance_block_trader_spot_quote_budget_pct
binance_block_trader_spot_min_quote_budget_usdt
binance_block_trader_spot_max_quote_budget_usdt
binance_block_trader_upbit_quote_budget_pct
binance_block_trader_upbit_min_quote_budget_krw
binance_block_trader_upbit_max_quote_budget_krw
binance_block_trader_futures_quote_budget_pct
binance_block_trader_futures_min_quote_budget_usdt
binance_block_trader_futures_max_quote_budget_usdt
binance_block_trader_budget_performance_scale_enabled
binance_block_trader_budget_performance_scale_min_samples
binance_block_trader_budget_performance_scale_win_rate_pct
binance_block_trader_budget_performance_scale_multiplier
binance_block_trader_execution_defect_loss_multiplier
binance_block_trader_performance_scorecard_feedback_limit
binance_block_trader_account_risk_pct
binance_block_trader_max_total_exposure_usdt
binance_block_trader_max_symbol_exposure_pct
binance_block_trader_min_reward_risk
binance_block_trader_volatile_attack_enabled
binance_block_trader_volatile_attack_candidate_limit
binance_block_trader_volatile_attack_budget_multiplier
binance_block_trader_volatile_attack_min_change_pct
binance_block_trader_volatile_attack_min_volume_expansion
binance_block_trader_volatile_attack_min_reward_risk
binance_block_trader_volatile_attack_stop_multiplier
binance_block_trader_daily_loss_stop_pct
binance_block_trader_monthly_loss_stop_pct
binance_block_trader_quote_retention_days
binance_block_trader_manager_run_retention_days
binance_block_trader_archive_retention_days
binance_block_trader_retention_interval_sec
crypto_market_research_enabled
crypto_market_research_once
crypto_market_research_db_path
crypto_market_research_state_path
crypto_market_research_universe
crypto_market_research_max_symbols
crypto_market_research_auto_universe_enabled
crypto_market_research_auto_universe_limit
crypto_market_research_research_universe_limit
crypto_market_research_llm_top_symbols
crypto_market_research_min_quote_volume_usdt
crypto_market_research_kline_intervals
crypto_market_research_kline_hot_window_rows
crypto_market_research_market_hot_window_rows
crypto_market_research_regime_enabled
crypto_market_research_squeeze_guard_enabled
crypto_market_research_collect_symbol_timeout_sec
crypto_market_research_collect_cycle_timeout_sec
crypto_market_research_collect_concurrency
crypto_market_research_feature_interval_sec
crypto_market_research_llm_interval_sec
crypto_market_research_llm_model
crypto_market_research_llm_reasoning_effort
crypto_market_research_external_enabled
crypto_market_research_external_sources
crypto_market_research_retention_days
crypto_market_research_archive_retention_days
crypto_quant_enabled
crypto_quant_db_path
crypto_quant_context_limit
crypto_quant_hot_window_rows
crypto_quant_archive_window_rows
crypto_quant_retention_days
crypto_quant_archive_retention_days
crypto_pattern_lab_enabled
crypto_pattern_lab_once
crypto_pattern_lab_state_path
crypto_pattern_lab_db_path
kr_equity_pattern_lab_db_path
kr_equity_pattern_lab_enabled
kr_equity_pattern_lab_min_samples
crypto_pattern_lab_strategy_paths
crypto_pattern_lab_freqtrade_data_paths
crypto_pattern_lab_interval_sec
crypto_pattern_lab_max_symbols
crypto_pattern_lab_intervals
crypto_pattern_lab_lookback_bars
crypto_pattern_lab_context_limit
crypto_pattern_lab_retention_days
crypto_pattern_lab_backtests_per_tuple_retention
crypto_pattern_lab_optimizer_runs_per_tuple_retention
crypto_pattern_lab_optimizer_trials_per_run_retention
crypto_pattern_lab_max_backtest_rows
crypto_pattern_lab_max_optimizer_runs
crypto_pattern_lab_max_optimizer_trials
crypto_pattern_lab_optimizer_enabled
crypto_pattern_lab_optimizer_max_scorecards
crypto_pattern_lab_optimizer_max_trials_per_scorecard
crypto_alpha_enabled
crypto_alpha_once
crypto_alpha_db_path
crypto_alpha_state_path
crypto_alpha_source_ids
crypto_alpha_crawl_interval_sec
crypto_alpha_outcome_interval_sec
crypto_alpha_rate_limit_sec
crypto_alpha_context_limit
crypto_alpha_llm_model
crypto_alpha_llm_reasoning_effort
etf_research_db_path
etf_research_universe
etf_research_max_symbols
etf_research_auto_collect
etf_research_stale_sec
etf_research_auto_min_interval_sec
etf_research_retention_days
etf_research_archive_retention_days
investment_memory_enabled
investment_memory_once
investment_memory_root_path
investment_memory_db_path
investment_memory_state_path
investment_memory_poll_interval_sec
investment_memory_send_telegram
investment_memory_run_daily_discovery
investment_memory_policy_mode
investment_memory_persona_tone
investment_memory_context_max_chars
investment_memory_ops_summary_cache_ttl_sec
investment_memory_compaction_interval_sec
investment_memory_policy_retired_keep
investment_memory_validation_event_retained_rows_per_venue
investment_memory_run_recent_rows_per_group
investment_memory_symbol_analysis_recent_rows_per_symbol
live_evaluator_enabled
live_evaluator_once
live_evaluator_db_path
live_performance_db_path
trading_validation_db_path
trading_validation_max_age_sec
trading_validation_payload_compaction_enabled
trading_validation_payload_recent_rows_per_group
trading_validation_payload_max_rows_per_group
trading_validation_payload_compact_min_chars
live_evaluator_state_path
live_evaluator_interval_sec
live_authority_max_scale_multiplier
live_authority_min_samples_to_scale
binance_validation_spot_fee_rate
binance_validation_futures_fee_rate
binance_validation_slippage_bps
binance_validation_initial_equity_usdt
kis_validation_buy_fee_rate
kis_validation_sell_fee_rate
kis_validation_sell_tax_rate
kis_validation_slippage_bps
kis_validation_spread_bps
kis_validation_initial_equity_krw
watchdog_enabled
watchdog_once
watchdog_interval_sec
watchdog_cooldown_sec
watchdog_flap_window_sec
watchdog_max_restarts_per_window
watchdog_state_path
watchdog_db_path
watchdog_runner_keys
daily_discovery_enabled
daily_discovery_db_path
daily_discovery_kospi_count
daily_discovery_kosdaq_count
daily_discovery_etf_count
daily_discovery_exclude_recent_days
daily_discovery_candidate_limit_per_market
naver_reports_enabled
naver_reports_db_path
naver_reports_seed_url
naver_reports_seed_urls
naver_reports_interval_sec
naver_reports_cycle_timeout_sec
naver_reports_state_path
naver_reports_heartbeat_interval_sec
naver_reports_worker_terminate_grace_sec
naver_reports_max_pages
naver_reports_since_date
naver_reports_request_delay_sec
naver_reports_pdf_archive_dir
naver_reports_min_pdf_text_chars
naver_reports_llm_facts_enabled
rag_enabled
rag_persist_path
rag_collection_name
rag_sync_chunk_limit
rag_sync_batch_size
rag_skip_existing
rag_query_top_k
rag_query_oversample_factor
rag_allow_legacy_pickle_migration
market_intelligence_sources_json
strategy_insight_sources_json
strategy_insight_collect_interval_sec
strategy_insight_error_backoff_sec
strategy_insight_request_timeout_sec
strategy_insight_once
strategy_insight_state_path
strategy_insight_db_path
strategy_insight_retention_days
strategy_insight_signal_row_cap_per_symbol
strategy_insight_sidecar_max_lines
strategy_insight_migrate_legacy_jsonl
valuation_db_path
valuation_watchlist
valuation_timeout_sec
valuation_min_refresh_hours
valuation_max_symbols_per_collect
valuation_auto_collect
valuation_auto_min_interval_sec
valuation_auto_max_symbols
market_judge_enabled
market_judge_once
market_judge_db_path
market_judge_state_path
market_quote_interval_sec
market_judge_interval_sec
market_judge_quote_retention_days
market_judge_quote_archive_retention_days
market_judge_account_retention_days
market_judge_judgment_retention_days
market_judge_judgment_archive_retention_days
market_judge_compact_recent_run_count
market_judge_compact_min_chars
market_judge_compact_symbol_min_chars
market_judge_max_symbols
market_judge_llm_max_symbols
market_judge_prompt_target_chars
market_judge_prompt_warn_chars
market_judge_prompt_max_chars
market_judge_use_naver_fallback
market_judge_query
market_pulse_enabled
market_pulse_once
market_pulse_db_path
market_pulse_state_path
market_pulse_interval_sec
market_pulse_closed_interval_sec
market_pulse_retention_days
market_pulse_archive_retention_days
market_pulse_timeout_sec
market_pulse_index_codes
market_pulse_sector_signal_limit
market_pulse_investor_flow_enabled
market_pulse_investor_flow_markets
market_pulse_program_trading_enabled
market_pulse_program_trading_markets
market_pulse_fx_enabled
host
port
allow_origins
reports_api_host
reports_api_port
reports_api_token
reports_api_tokens
reports_ui_allowed_cidrs
reports_ui_trust_proxy
reports_worker_state_path
```

Runtime archive maintenance is dry-run by default:

```bash
tradecraft-runtime-archive status
tradecraft-runtime-archive migrate
tradecraft-runtime-archive migrate --apply
tradecraft-runtime-archive verify
tradecraft-runtime-archive restore ENTRY_ID EMPTY_DESTINATION
```

`migrate --apply` archives and verifies eligible dry-run/RAG artifacts and old
rejected Jue Wiki selection rows before removing hot copies. `restore` refuses a
non-empty destination. Neither command changes trading settings or invokes a
manager, executor, tick, or order endpoint.

## Refactor Rules

- Preserve env aliases where tests expect them.
- Do not commit real secrets.
- Settings visible in UI must map clearly to `AppSettings`.
- Changes to trading toggles require tests and readiness display updates.
- Keep KIS, Binance, crypto research, memory, market, reports/RAG, settings, and LLM usage settings grouped for operators; do not flatten them into generic key/value noise.
- Any settings-catalog change for danger/high-risk fields must preserve UI confirmation semantics.
