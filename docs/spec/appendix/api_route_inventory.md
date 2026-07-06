# API Route Inventory

Captured from `tradecraft.main.app.routes`.

Capture command:

```bash
PYTHONPATH=src python3 - <<'PY' > /tmp/hermes_routes.txt
from tradecraft.main import app
routes = sorted(
    app.routes,
    key=lambda route: (
        getattr(route, "path", ""),
        ",".join(sorted(getattr(route, "methods", []) or [])),
        getattr(route, "name", ""),
    ),
)
for route in routes:
    methods = ",".join(sorted(getattr(route, "methods", []) or []))
    path = getattr(route, "path", "")
    name = getattr(route, "name", "")
    print(f"{methods:20} {path:70} {name}")
PY
```

Capture note: import succeeded and route inventory was captured. Stderr emitted a telemetry warning: `Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given`.

```text
GET                  /                                                                      index
GET                  /api/backtest/data-status                                              backtest_data_status
GET                  /api/backtest/scenarios                                                backtest_scenarios
POST                 /api/backtest/start                                                    backtest_start
GET                  /api/backtest/status                                                   backtest_status
POST                 /api/backtest/stop                                                     backtest_stop
GET                  /api/binance/blocks                                                    binance_blocks
POST                 /api/binance/blocks/adopt-existing/run-once                            binance_blocks_adopt_existing_run_once
POST                 /api/binance/blocks/executor/tick                                      binance_blocks_executor_tick
POST                 /api/binance/blocks/kill-switch                                        binance_blocks_kill_switch
POST                 /api/binance/blocks/kill-switch/release                                binance_blocks_kill_switch_release
POST                 /api/binance/blocks/manager/run-once                                   binance_blocks_manager_run_once
GET                  /api/binance/blocks/status                                             binance_blocks_status
GET                  /api/binance/patterns/context                                          binance_pattern_context
GET                  /api/binance/quant/signals                                             binance_quant_signals
POST                 /api/codex/native/check                                                codex_native_check
GET                  /api/codex/native/status                                               codex_native_status
POST                 /api/crypto/alpha/collect                                              crypto_alpha_collect
GET                  /api/crypto/alpha/context                                              crypto_alpha_context
POST                 /api/crypto/alpha/outcomes/run-once                                    crypto_alpha_outcomes_run_once
GET                  /api/crypto/alpha/status                                               crypto_alpha_status
GET                  /api/crypto/pattern-lab/status                                         crypto_pattern_lab_status_alias
POST                 /api/crypto/research/collect                                           crypto_research_collect
GET                  /api/crypto/research/context                                           crypto_research_context
POST                 /api/crypto/research/run-once                                          crypto_research_run_once
GET                  /api/crypto/research/status                                            crypto_research_status
GET                  /api/dashboard                                                         dashboard
GET                  /api/discovery/latest                                                  daily_discovery_latest
POST                 /api/discovery/run-once                                                daily_discovery_run_once
GET                  /api/discovery/status                                                  daily_discovery_status
GET                  /api/etf/research/candidates                                           etf_research_candidates
POST                 /api/etf/research/collect                                              etf_research_collect
GET                  /api/etf/research/status                                               etf_research_status
GET                  /api/evidence-policy/context                                           evidence_policy_context
GET                  /api/evidence-policy/status                                            evidence_policy_status
GET                  /api/health                                                            health
POST                 /api/helper/ask                                                        helper_ask
GET                  /api/jue/lifecycle/latest                                              jue_lifecycle_latest
GET                  /api/jue/source-manifest                                               jue_source_manifest
GET                  /api/jue/workflows/status                                              jue_workflows_status
GET                  /api/kis/blocks                                                        kis_blocks
POST                 /api/kis/blocks/adopt-existing/run-once                                kis_blocks_adopt_existing_run_once
POST                 /api/kis/blocks/executor/tick                                          kis_blocks_executor_tick
POST                 /api/kis/blocks/kill-switch                                            kis_blocks_kill_switch
POST                 /api/kis/blocks/kill-switch/release                                    kis_blocks_kill_switch_release
POST                 /api/kis/blocks/manager/run-once                                       kis_blocks_manager_run_once
POST                 /api/kis/blocks/orders/{order_id}/cancel                               kis_block_order_cancel
GET                  /api/kis/blocks/status                                                 kis_blocks_status
GET                  /api/kis/blocks/{block_id}                                             kis_block_detail
POST                 /api/kis/blocks/{block_id}/close                                       kis_block_close
POST                 /api/kis/blocks/{block_id}/directive                                   kis_block_directive
POST                 /api/kis/blocks/{block_id}/pause                                       kis_block_pause
POST                 /api/kis/blocks/{block_id}/resume                                      kis_block_resume
GET                  /api/live/authority                                                    live_authority
POST                 /api/llm/probe                                                         llm_probe
GET                  /api/llm/status                                                        llm_usage_status
GET                  /api/llm/usage                                                         llm_usage_legacy_summary
GET                  /api/llm/usage/daily                                                   llm_usage_legacy_summary
GET                  /api/llm/usage/status                                                  llm_usage_status
GET                  /api/llm/usage/summary                                                 llm_usage_summary
GET                  /api/llm/usage/today                                                   llm_usage_today
GET                  /api/market/account                                                    market_account
GET                  /api/market/clock                                                      market_clock
GET                  /api/market/judgments/latest                                           market_judgment_latest
POST                 /api/market/judgments/run-once                                         market_judgment_run_once
GET                  /api/market/judgments/schedule                                         market_judgment_schedule
GET                  /api/market/pulse/history                                              market_pulse_history
GET                  /api/market/pulse/latest                                               market_pulse_latest
POST                 /api/market/pulse/run-once                                             market_pulse_run_once
GET                  /api/market/pulse/status                                               market_pulse_status
GET                  /api/market/quotes                                                     market_quotes
GET                  /api/memory/blocks/{block_id}                                          investment_memory_block
POST                 /api/memory/init                                                       investment_memory_init
GET                  /api/memory/policies/revisions                                         investment_memory_policy_revisions
POST                 /api/memory/policies/revisions/{revision_id}/activate                  investment_memory_policy_revision_activate
POST                 /api/memory/policies/revisions/{revision_id}/reject                    investment_memory_policy_revision_reject
GET                  /api/memory/policies/rules                                             investment_memory_policy_rules
GET                  /api/memory/policies/scorecards                                        investment_memory_policy_scorecards
POST                 /api/memory/reflections/run-due                                        investment_memory_reflections_run_due
GET                  /api/memory/replays/history                                            investment_memory_replay_history
GET                  /api/memory/replays/latest                                             investment_memory_replay_latest
POST                 /api/memory/replays/run-once                                           investment_memory_replay_run_once
GET                  /api/memory/reviews/history                                            investment_memory_review_history
GET                  /api/memory/reviews/latest                                             investment_memory_review_latest
POST                 /api/memory/reviews/run-once                                           investment_memory_review_run_once
POST                 /api/memory/rituals/run-once                                           investment_memory_ritual_run_once
POST                 /api/memory/seed-current                                               investment_memory_seed_current
GET                  /api/memory/status                                                     investment_memory_status
GET                  /api/memory/symbols/{symbol}                                           investment_memory_symbol
GET                  /api/memory/today                                                      investment_memory_today
POST                 /api/memory/update/run-once                                            investment_memory_update_run_once
GET                  /api/ops/codex-native/status                                           codex_native_status
GET                  /api/ops/processes                                                     ops_processes
GET                  /api/ops/readiness                                                     ops_readiness
POST                 /api/ops/restart                                                       ops_restart
GET                  /api/ops/system-metrics                                                ops_system_metrics
GET                  /api/ops/watchdog/status                                               ops_watchdog_status
GET                  /api/portfolio                                                         dashboard
GET                  /api/portfolio-coach/review-queue                                      portfolio_coach_review_queue
POST                 /api/portfolio-coach/review-queue/{message_id}/approve                 portfolio_coach_review_approve
POST                 /api/portfolio-coach/review-queue/{message_id}/reject                  portfolio_coach_review_reject
GET                  /api/rag/search                                                        rag_search
GET                  /api/rag/status                                                        rag_status
POST                 /api/rag/sync                                                          rag_sync
GET                  /api/rebalance/kis-status                                              rebalance_kis_status
POST                 /api/reports/backfill-symbol-links                                     reports_backfill_symbol_links
POST                 /api/reports/crawl-once                                                reports_crawl_once
POST                 /api/reports/repair-metadata                                           reports_repair_metadata
GET                  /api/reports/search                                                    reports_search
GET                  /api/reports/status                                                    reports_status
GET                  /api/research/ask                                                      research_ask
GET                  /api/research/status                                                   research_status
GET                  /api/runtime/processes                                                 ops_processes
GET                  /api/runtime/status                                                    runtime_storage_status
GET                  /api/runtime/storage                                                   runtime_storage_status
GET                  /api/runtime/storage-cleanup                                           runtime_storage_cleanup_legacy_dry_run
POST                 /api/runtime/storage/cleanup                                           runtime_storage_cleanup
GET                  /api/runtime/storage/status                                            runtime_storage_status
GET                  /api/settings/catalog                                                  settings_catalog
PATCH                /api/settings/values                                                   settings_update
GET                  /api/strategy/brief                                                    strategy_brief
POST                 /api/strategy/brief                                                    strategy_brief_post
GET                  /api/strategy/candidates                                               strategy_candidates
POST                 /api/strategy/candidates                                               strategy_candidates_post
GET                  /api/strategy/insights                                                 strategy_insights
POST                 /api/strategy/insights/collect                                         strategy_insights_collect
GET                  /api/strategy/insights/signals                                         strategy_insight_signals
GET                  /api/strategy/insights/status                                          strategy_insights_status
POST                 /api/strategy/insights/{source_id}                                     strategy_insight_append
POST                 /api/strategy/intent                                                   strategy_intent
POST                 /api/symbols/fundamentals/collect                                      symbol_fundamentals_collect
GET                  /api/symbols/fundamentals/status                                       symbol_fundamentals_status
GET                  /api/symbols/special-watch                                             symbol_analysis_special_watch
GET                  /api/symbols/{symbol}/analysis/history                                 symbol_analysis_history
POST                 /api/symbols/{symbol}/analysis/run                                     symbol_analysis_run
GET                  /api/symbols/{symbol}/fundamentals                                     symbol_fundamentals
GET                  /api/telegram/status                                                   telegram_status
POST                 /api/telegram/webhook                                                  telegram_webhook
GET                  /api/trading/validation                                                trading_validation_status_alias
POST                 /api/trading/validation/run-once                                       trading_validation_run_once
GET                  /api/trading/validation/status                                         trading_validation_status
GET                  /api/wiki/application/effectiveness                                    wiki_application_effectiveness
GET                  /api/wiki/application/status                                           wiki_application_status
GET                  /api/wiki/context                                                      wiki_context
POST                 /api/wiki/lint                                                         wiki_lint
GET                  /api/wiki/lint/findings                                                wiki_lint_findings
GET                  /api/wiki/pages/{page_id}                                              wiki_page
GET                  /api/wiki/pages/{page_id}/sources                                      wiki_page_sources
POST                 /api/wiki/rebuild                                                      wiki_rebuild
POST                 /api/wiki/repair/run-once                                              wiki_repair_run_once
GET                  /api/wiki/search                                                       wiki_search
GET                  /api/wiki/status                                                       wiki_status
GET                  /apple-touch-icon-precomposed.png                                      icon_fallback
GET                  /apple-touch-icon.png                                                  icon_fallback
GET,HEAD             /docs                                                                  swagger_ui_html
GET,HEAD             /docs/oauth2-redirect                                                  swagger_ui_redirect
GET                  /favicon.ico                                                           icon_fallback
GET,HEAD             /openapi.json                                                          openapi
GET,HEAD             /redoc                                                                 redoc_html
                     /static                                                                static
```
