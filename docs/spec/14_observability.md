# Observability

HERMES/쥬 observability is for an active block-trading system: the checks below are meant to tell an operator whether KIS and Binance block managers, deterministic executors, memory, market context, safety gates, and telemetry are healthy enough for supervised trading work.

## Operator Surfaces

- Web UI status banners.
- `/api/ops/readiness`.
- `/api/live/authority`.
- `/api/*/status` endpoints.
- `.runtime/*.json` runner status files.
- Process logs when the local launcher redirects stdout/stderr into `.runtime/*.log`.
- `.runtime/llm_usage.db` usage telemetry.

The public `/api/health` endpoint only confirms the control service and points to the protected readiness endpoint. Detailed trading readiness belongs in `/api/ops/readiness`, which is admin protected and includes process status, admin-token configuration, KIS/Binance live-order readiness, kill switches, memory seeding, live evaluator state, market services, LLM readiness, LLM usage, KIS rate-limit settings, and enabled runner health.

`/api/live/authority` is the protected evaluator readout. It includes enabled
state, edge DB status, performance summary, config, and per-venue authority
packets for KIS and Binance. Operators should review it before trusting manager
scale decisions because the packet carries `live_grade`,
`max_budget_multiplier`, `allow_scale_up`, and scorecards.

The static UI has an auth banner and an ops banner. `authBanner` warns when admin authentication is missing or rejected. `opsBanner` summarizes readiness as green/yellow/red, live-trading versus paper mode, memory seed status, market judge/pulse status, blockers, warnings, and stale or stopped runners. The runtime helper tab also shows runtime storage, runner state, `.runtime` size, and operational chips.

Important status endpoints include:

- `/api/kis/blocks/status`: KIS block ledger status, next manager run, market-judge schedule, stale-process list, and embedded readiness.
- `/api/binance/blocks/status`: Binance block snapshot/status, compact mode support, and Binance readiness details.
- `/api/llm/status` and `/api/llm/probe`: native LLM runtime status and explicit probe execution.
- `/api/llm/usage/status` and `/api/llm/usage/summary`: LLM telemetry status and per-day usage summaries.
- `/api/live/authority`: live evaluator, edge scorecard, performance summary, and KIS/Binance authority packets.
- `/api/rebalance/kis-status`, `/api/reports/status`, `/api/rag/status`, `/api/runtime/storage`, `/api/telegram/status`, and the crypto research/alpha status routes as supporting surfaces.

Runtime process status is written through `.runtime/pids/*.pid` and checked by `runner_process_status()`. Readiness also detects stale live processes when code changed after process start. Runner state snapshots live in `.runtime/*.json` files such as `.runtime/kis_block_trader.json`, `.runtime/binance_block_trader.json`, `.runtime/investment_memory_runner.json`, `.runtime/live_evaluator.json`, `.runtime/market_judge.json`, `.runtime/market_pulse.json`, `.runtime/crypto_market_research.json`, and `.runtime/crypto_alpha.json`. Process log files depend on the launcher; when stdout/stderr is redirected locally, check the corresponding `.runtime/*.log` file rather than assuming a fixed filename.

`llm_usage.db` stores per-call `component`, `operation`, `model`, `mode`, `status`, latency, token counts, usage source, character counts, error message, metadata, and KST trading day. The readiness payload embeds today's summary so excessive LLM usage, missing token accounting, or non-ok statuses can be reviewed without opening the DB first.

## Daily Checks

```bash
curl -s http://127.0.0.1:18080/api/health
curl -s -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" http://127.0.0.1:18080/api/ops/readiness
curl -s -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" http://127.0.0.1:18080/api/live/authority
sqlite3 .runtime/llm_usage.db ".tables"
sqlite3 .runtime/kis_blocks.db "select status, created_by, count(*) from blocks group by status, created_by;"
sqlite3 .runtime/binance_blocks.db "select status, market, created_by, count(*) from blocks group by status, market, created_by;"
sqlite3 .runtime/live_performance.db "select venue, attribution, include_in_jue_alpha, count(*) from live_block_performance group by venue, attribution, include_in_jue_alpha;"
sqlite3 .runtime/live_edge.db "select venue, grade, count(*) from live_edge_scorecards group by venue, grade;"
```

Also check the KIS and Binance block status endpoints before trusting the UI summary:

```bash
curl -s -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" http://127.0.0.1:18080/api/kis/blocks/status
curl -s -H "Authorization: Bearer $TRADECRAFT_ADMIN_TOKEN" "http://127.0.0.1:18080/api/binance/blocks/status?compact=1"
sqlite3 .runtime/kis_blocks.db "select key, value_json, updated_at from system_state;"
sqlite3 .runtime/binance_blocks.db "select enabled, reason, updated_at from kill_switch;"
```

The KIS check should confirm `kill_switch`, `open_block_count`, `waiting_entry_block_count`, pending orders, latest manager run status, KIS account readiness, and rate-limit configuration. The Binance check should confirm `kill_switch`, spot/futures execution mode, open/proposed block counts, latest manager run status, precision/filter-related errors, and risk settings.

Review current runner state and logs:

```bash
find .runtime/pids -maxdepth 1 -type f -print -exec cat {} \;
find .runtime -maxdepth 1 -name "*.json" -type f | sort
find .runtime -maxdepth 1 -name "*.log" -type f | sort
# Then tail the log file used by the launcher for the runner being inspected.
```

## Performance Review Checks

- Separate KIS existing-position adoption from LLM-created blocks.
- Separate Binance wallet adoption from LLM-created blocks.
- Separate failed-entry simulation/reflection from realized PnL.
- Track open unrealized PnL separately from closed realized PnL.
- Review R-multiple and average win/loss, not only win rate.
- Review live authority grade and scorecard count before allowing larger
  manager budgets.

KIS adopted holdings use `created_by='existing_position'`; Binance spot wallet adoption uses `created_by='wallet_adoption'`. These records are operationally useful because 쥬 can manage inherited risk, but pre-adoption gains or losses must be excluded from 쥬-created alpha and from manager performance scorecards unless the report explicitly labels them as adopted-position management.

When `live_block_performance` is populated, use its inclusion flags as the
preferred cross-venue summary contract:

- `include_in_jue_alpha` for Jue-created edge;
- `include_in_risk_management` for inherited or open risk handling;
- `include_in_execution_quality` for rejected, failed, unfilled, or pre-fill
  operational outcomes.

When `live_edge_scorecards` is populated, treat `restricted` and `observe_only`
as manager authority limits. Treat `scale_candidate` as permission to consider
scaling only within `max_budget_multiplier`, sample-size, and venue safety
constraints.

Failed-entry records and reflections are decision/process evidence. They should not be counted as realized trading losses unless an entry order was actually filled or a paper-opened block intentionally represents the fill. For active trading review, separate:

- LLM-created realized closed blocks.
- Adopted KIS existing positions.
- Adopted Binance wallet positions.
- Open unrealized KIS/Binance blocks.
- Failed entries, rejected orders, canceled triggers, and precision/filter failures.

Accounting exactness remains venue-specific. KIS KRW PnL can be affected by taxes, fees, settlement timing, available quantity, and reconciliation. Binance USDT PnL can be affected by spot versus futures semantics, fees, funding, leverage, liquidation distance, precision filters, and wallet cost-basis estimation.

## Error Review Checks

- LLM timeouts.
- Exchange order rejects.
- Quote stale/error.
- Reflection backlog.
- Process stale/restart needed.
- Live evaluator stopped, disabled, stale, or missing scorecards.
- UI auth failures.
- Excessive LLM usage.

For KIS, prioritize quote/order API rejects, token or rate-limit failures, order reconciliation mismatches, cash/quantity gate failures, kill-switch state, and stale market-judge or market-pulse context. For Binance, prioritize exchange filter and precision rejects, spot/futures credential readiness, open-order mismatches, futures position risk, exposure caps, liquidation-distance warnings, kill-switch state, and stale crypto research/alpha/quant context.

Memory review should include `pending_reflection_count`, `reflection_count`, `scorecard_count`, active policy-rule counts, and any LLM errors in reflection runs. UI review should include auth banner behavior, ops banner severity, helper runtime status, and whether KIS/Binance block boards distinguish open, proposed, history, adopted, and failed-entry records clearly enough for an operator to act.
