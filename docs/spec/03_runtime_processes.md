# Runtime Processes

## Process Catalog

| Process | Source | Responsibility | Typical Cadence | Critical DBs/State |
| --- | --- | --- | --- | --- |
| `tradecraft-control` | `tradecraft.main:run` | Web/API control surface, static UI, readiness, settings, operator actions | Always on | All service DBs, `.runtime/pids/tradecraft-control.pid` |
| `tradecraft-ui` | `tradecraft.main:run` | Alias for the main FastAPI/static UI process | Always on when used instead of `tradecraft-control` | Same as `tradecraft-control` |
| `tradecraft-runtime` | `tradecraft.runtime.runner:run` | Generic runtime engine/session state writer | `runtime_write_interval_sec`, minimum 1 second | `runtime_state_path`, runtime session config |
| `tradecraft-intelligence` | `tradecraft.runtime.intelligence_runner:run` | Alias over the research/advice loop with service name `tradecraft-intelligence`; report collection is intentionally disabled here to avoid duplicate crawls | `research_run_interval_sec`, minimum 300 seconds | `research_state_path`; reads `naver_reports.db` and RAG context written by the reports runner |
| `tradecraft-research` | `tradecraft.runtime.research_runner:run` | Legacy research/advice loop; report collection only runs when `TRADECRAFT_RESEARCH_RUNNER_COLLECT_REPORTS=true` is explicitly set | `research_run_interval_sec`, and optionally `naver_reports_interval_sec`, minimum 300 seconds | `research_state_path`, optional `naver_reports.db`/RAG writes in legacy collect mode, strategy Markdown |
| `tradecraft-naver-reports` | `tradecraft.runtime.naver_reports_runner:run` | Primary Naver report crawler and RAG sync runner | `naver_reports_interval_sec`, minimum 300 seconds | `naver_reports.db`, RAG store when enabled |
| `tradecraft-strategy-insights` | `tradecraft.runtime.strategy_insights_runner:run` | Whale/세시반 and configured strategy signal collection | `strategy_insight_collect_interval_sec`, minimum 30 seconds, with source-aware backoff | `strategy_insights.db`, `strategy_insight_state_path` |
| `tradecraft-kis-block-trader` | `tradecraft.runtime.kis_block_trader_runner:run` | KIS quote/rule ticks, manager runs, ETF auto-collect hook, block adoption/execution | Rule loop from `kis_block_trader_rule_interval_sec`; manager from KRX session logic and `kis_block_trader_manager_interval_sec` | `kis_blocks.db`, `investment_memory.db`, `kis_block_trader_state_path` |
| `tradecraft-binance-block-trader` | `tradecraft.runtime.binance_block_trader_runner:run` | Binance spot/futures rule ticks, manager runs, wallet adoption, performance feedback, retention cleanup | Rule loop from `binance_block_trader_rule_interval_sec`, minimum 5 seconds; manager from `binance_block_trader_manager_interval_sec`, minimum 60 seconds | `binance_blocks.db`, `investment_memory.db`, crypto DBs, `binance_block_trader_state_path` |
| `tradecraft-investment-memory` | `tradecraft.runtime.investment_memory_runner:run` | Rituals, reflections, memory updates, policy scorecards, optional daily discovery | `investment_memory_poll_interval_sec`, minimum 10 seconds | `investment_memory.db`, `.runtime/investment_memory`, `investment_memory_state_path` |
| `tradecraft-jue-wiki` | `tradecraft.runtime.jue_wiki_runner:run` | Rebuilds and lints scoped Jue Wiki pages from RAG, block ledgers, and memory reflections | `jue_wiki_runner_interval_sec`, minimum 300 seconds | `.runtime/jue_wiki/wiki.db`, `.runtime/jue_wiki/`, `.runtime/jue_wiki_runner.json` |
| `tradecraft-live-evaluator` | `tradecraft.runtime.live_evaluator_runner:run` | Live block-performance summary, edge scorecard status, KIS/Binance authority packet state | `live_evaluator_interval_sec`, minimum 30 seconds | `live_performance.db`, `live_edge.db`, `live_evaluator_state_path` |
| `tradecraft-market-judge` | `tradecraft.runtime.market_judge_runner:run` | KIS account/quote/market judgment loop | Quote loop from `market_quote_interval_sec`, minimum 15 seconds; LLM judge from KRX session logic and `market_judge_interval_sec` | `market_judgment.db`, `market_pulse.db`, `investment_memory.db`, `market_judge_state_path` |
| `tradecraft-market-pulse` | `tradecraft.runtime.market_pulse_runner:run` | Korean market regime, index, sector, investor-flow, program trading, FX pulse | `market_pulse_interval_sec`, minimum 30 seconds | `market_pulse.db`, `market_pulse_state_path` |
| `tradecraft-crypto-market-research` | `tradecraft.runtime.crypto_market_research_runner:run` | Crypto feature collection, market structure, external context, LLM research, optional quant repository | Feature loop from `crypto_market_research_feature_interval_sec`, minimum 60 seconds; LLM loop from `crypto_market_research_llm_interval_sec`, minimum 300 seconds | `crypto_market_research.db`, `crypto_quant.db`, `crypto_market_research_state_path` |
| `tradecraft-crypto-pattern-lab` | `tradecraft.runtime.crypto_pattern_lab_runner:run` | Pattern import/backtest from configured strategy/data paths | `crypto_pattern_lab_interval_sec`, minimum 60 seconds | `crypto_pattern_lab.db`, `crypto_pattern_lab_state_path`; currently state-file tracked rather than PID-tracked by `process_status.py` |
| `tradecraft-crypto-alpha` | `tradecraft.runtime.crypto_alpha_runner:run` | Crypto alpha source crawl and outcome labeling | Crawl loop from `crypto_alpha_crawl_interval_sec`, minimum 300 seconds; outcome loop from `crypto_alpha_outcome_interval_sec`, minimum 60 seconds | `crypto_alpha.db`, `crypto_alpha_state_path` |
| `tradecraft-reports-api` | `tradecraft.reports_api.main:run` | Separate reports console API, saved views, data quality, deployment checks | Always on when reports console is used | `naver_reports.db`, RAG store, `.runtime/reports_saved_views.json` |
| `tradecraft-reports-worker` | `tradecraft.reports_api.worker:run` | Reports collection worker and RAG sync for the reports console | `naver_reports_interval_sec`, minimum 300 seconds | `naver_reports.db`, RAG store, `reports_worker_state_path` |
| `tradecraft-reports-stack` | `tradecraft.reports_api.stack:run` | Reports UI build/preflight plus reports API and worker subprocess launcher | Supervises reports API and worker until either exits | Reports API and worker state |

## KRX-Oriented Timing

- Market clock source: `build_market_clock` in `src/tradecraft/services/market_judgment.py`, timezone `Asia/Seoul`.
- Closed days: weekends and KRX holidays when the holiday calendar is available.
- Pre-open ritual: `08:30-09:00 KST`, one manager/judgment slot per trading day.
- Opening quote-only guard: regular session begins at `09:00 KST`, but market judge suppresses LLM judgment before `09:05 KST`.
- Regular market manager cadence: configured manager/judge interval, commonly 30 minutes.
- Quote/rule tick: shorter deterministic loop, independent from LLM manager.
- Closing watch: `15:20-15:30 KST`; KIS block manager does not start new manager runs here, while existing block risk management continues through executor ticks.
- Post-close review: `15:30-16:00 KST` session, with schedule text recording the `15:45 KST` review slot once per trading day.
- Outside those windows, KRX-oriented manager and judgment LLM work is closed unless explicitly invoked through an API action.

## Binance Timing

- Binance is 24h and does not use KRX sessions.
- Binance 쥬 uses its own model, reasoning, rule interval, manager interval, quote universe, risk settings, and order execution toggles. The active default manager cadence is 30 minutes (`1800` seconds), while deterministic executor ticks remain faster and independent from the LLM manager.
- The Binance runner performs spot wallet adoption on first cycle or when manager work is due.
- The Binance executor tick runs every rule interval and can manage spot and futures blocks without waiting for an LLM manager run.
- Spot and futures blocks share Binance memory/process lessons but have separate market/risk constraints, especially leverage, liquidation distance, exchange filters, and exposure caps.
- Crypto market research, pattern lab, quant data, and alpha services are supporting context layers. They should be restarted before or alongside Binance 쥬 when stale research is a concern.

## Restart Order

1. `tradecraft-control`
2. `tradecraft-runtime`
3. `tradecraft-investment-memory`
4. `tradecraft-live-evaluator`
5. `tradecraft-naver-reports` for report/RAG freshness, plus one of `tradecraft-intelligence` or `tradecraft-research` only when the legacy research/advice loop is needed
6. `tradecraft-reports-api` plus `tradecraft-reports-worker`, or `tradecraft-reports-stack` when using the reports console stack launcher
7. `tradecraft-strategy-insights`
8. `tradecraft-market-pulse`
9. `tradecraft-market-judge`
10. `tradecraft-kis-block-trader`
11. `tradecraft-crypto-market-research`
12. `tradecraft-crypto-pattern-lab`
13. `tradecraft-crypto-alpha`
14. `tradecraft-binance-block-trader`

This order gives the control surface, memory layer, and live authority state a chance to expose evidence before active trading runners resume, then brings up KRX context before KIS block trading and crypto context before Binance block trading.

## Readiness Checks

- Check `/api/health` for the public control health payload and `/api/ops/readiness` with admin auth for the operational readiness payload.
- Check `/api/live/authority` with admin auth for the current KIS/Binance live-grade and budget authority packet.
- Check UI banners for auth, memory empty, stale process, kill switch, live/paper mode, and stale/error service states.
- Check `process_status.py` keys and `.runtime/pids/*.pid` files for expected runner identity: `control`, `runtime`, `intelligence`, `research`, `kis_block_trader`, `binance_block_trader`, `crypto_market_research`, `crypto_pattern_lab`, `crypto_alpha`, `jue_wiki`, `investment_memory`, `live_evaluator`, `naver_reports`, `strategy_insights`, `market_judge`, `market_pulse`, and `watchdog`.
- Check `.runtime/*.json` status files, especially `kis_block_trader_state_path`, `binance_block_trader_state_path`, `investment_memory_state_path`, `live_evaluator_state_path`, `market_judge_state_path`, `market_pulse_state_path`, `crypto_market_research_state_path`, `crypto_pattern_lab_state_path`, and `crypto_alpha_state_path`.
- Check runner logs in `.runtime/*.log` when launched by local process tooling.
- Check critical SQLite DBs for recent writes: `kis_blocks.db`, `binance_blocks.db`, `investment_memory.db`, `live_performance.db`, `live_edge.db`, `market_judgment.db`, `market_pulse.db`, `naver_reports.db`, `strategy_insights.db`, and crypto research/alpha/pattern DBs.
- For active order readiness, check exchange-specific credentials and readiness helpers in `AppSettings`: `kis_primary_ready`, `binance_spot_ready`, and `binance_futures_ready`.

## Failure Modes To Record During Refactor

- Manager LLM timeout or malformed action schema.
- Quote provider stale/error.
- KIS/Binance token refresh failure.
- Binance order precision/filter rejection.
- Binance leverage, liquidation-distance, or exposure-gate rejection.
- KIS order reconciliation mismatch.
- KIS pending order timeout or failed exit retry cooldown.
- Kill switch set in the KIS or Binance block ledger.
- Memory reflection backlog.
- Live evaluator stopped, disabled, stale, or serving empty `observe_only` packets because scorecards are missing.
- Research/RAG or Naver report freshness gaps feeding stale manager context.
- Market pulse or market judge state stale during a regular KRX session.
- PID file stale, missing, or mismatched with the actual process command.
- Process running with code older than touched runner/service files.
- UI compact refresh losing account state or hiding auth/stale/error states.

## Jue Wiki V3 Publication And Read Safety

The approved plan referred to `docs/spec/09_runtime_processes.md`; the active
repository specification is this file, `docs/spec/03_runtime_processes.md`.

`tradecraft-jue-wiki` is the only process that compiles, lints, publishes,
repairs, projects the index, and persists Wiki V3 operational health. Its stored
`OpsSectionSnapshotV1.v3` contains the configured active read mode, signed
per-venue eligibility, aggregate compatibility fields, and `by_scope` health for
KIS and Binance. Each scope records snapshot identity and creation time,
ingest/compile/lint/publish/projection state, index rebuild state, and
stale/conflicted/orphan/repair-backlog counts. Snapshot age displayed by ops is
informational; required-mode decisions recompute age from
`snapshot_created_at`, so a stopped runner cannot freeze a healthy age.

The control API, KIS runner, Binance runner, and market-judge provider only read
the stored snapshot and signed shadow eligibility on request paths. They never
compile, lint, repair, rebuild, initialize, or write Wiki storage while serving
context/readiness. Provider timeouts and stale-cache behavior remain intact.

Required mode permits new risk only when the requested venue scope exists, its
snapshot id matches the packet, live age is at most 3,600 seconds, all source and
publication stages are healthy, the index is operational, degradation counts
are zero, and the signed eligibility contract is fresh and valid. A projection
warning caused only by cleanup leaves the index operational but remains visible.
Missing/malformed status, Wiki DB outage, source/compiler/lint/index failure,
stale/conflicted support, or eligibility failure blocks create and
risk-increasing update actions. Risk-reducing updates, close/exit,
reconciliation, and kill-switch checks remain available.

The migration sequence is `shadow -> prefer -> required`; it is evidence-gated,
not time-gated. No runner or readiness response changes the live read setting
automatically. Recovery rolls back to `shadow`, restarts the Wiki runner, waits
for a fresh stored publication and signed comparisons, then reevaluates gates.
RAG continues only as a bounded repair, audit, backfill, and index-rebuild input.
