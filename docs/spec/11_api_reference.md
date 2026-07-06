# API Reference

## Auth Model

- Static files and `/api/health` are public.
- Operational endpoints require admin token where configured.
- Protected calls accept `Authorization: Bearer <token>` or `X-TradeCraft-Admin-Token`.
- The admin token source is `AppSettings.admin_token_list`, composed from `admin_tokens` and legacy `admin_token`.
- If no admin token is configured, protected endpoints return `401 admin auth required`; this is an unsafe local setup for an active trading system, not a recommended operating mode.
- Telegram webhook is public at the route layer but requires `X-Telegram-Bot-Api-Secret-Token`/secret validation and configured chat ID checks before processing commands.

## API Groups

| Group | Prefix | Purpose |
| --- | --- | --- |
| Health/Ops | `/api/health`, `/api/ops/*` | Readiness and runtime status. |
| Dashboard | `/api/dashboard` | Portfolio/dashboard data. |
| KIS Blocks | `/api/kis/blocks*` | KIS block status, list, detail, actions, manager/executor. |
| Binance Blocks | `/api/binance/blocks*` | Binance block status, list, detail, actions, manager/executor, spot/futures kill switch. |
| Binance Context | `/api/binance/quant/*`, `/api/binance/patterns/*`, `/api/crypto/*` | Crypto quant signals, pattern context, market research, and alpha context for Binance 쥬. |
| Market Judgment | `/api/market/*` | Market clock, quotes, account, market pulse, judgments. |
| Memory | `/api/memory/*` | Memory status, rituals, seed, reflections, reviews, policies. |
| Jue Wiki | `/api/wiki/status`, `/api/wiki/context`, `/api/wiki/search`, `/api/wiki/pages/{page_id}`, `/api/wiki/pages/{page_id}/sources`, `/api/wiki/application/status`, `/api/wiki/application/effectiveness`, `/api/wiki/rebuild`, `/api/wiki/lint`, `/api/wiki/lint/findings`, `/api/wiki/repair/run-once` | Wiki status, compact context, search, page reads, source references, applied-intelligence effectiveness, rebuild, lint, lint findings, and repair actions. Status is public; all context/content/search/source/lint-finding/application and mutation routes are admin protected. |
| Reports/RAG | `/api/reports/*`, `/api/rag/*` | Report collection, status, repair, search, RAG sync/search. |
| Strategy | `/api/strategy/*`, `/api/discovery/*`, `/api/symbols/*`, `/api/etf/*` | Candidates, brief, insights, daily discovery, symbol analysis/fundamentals, ETF research. |
| Settings | `/api/settings*` | Runtime settings catalog and updates. |
| LLM Runtime/Usage | `/api/llm/*` | Runtime status/probe plus usage summary/status for LLM-backed 쥬/research/memory flows. |
| Live Authority | `/api/live/authority` | Admin-protected live evaluator status, performance summary, edge status, config, and KIS/Binance authority packets. |
| Telegram | `/api/telegram/*` | Telegram status and webhook command ingress. |
| Runtime Storage | `/api/runtime/storage*` | Runtime storage report and cleanup. |
| Portfolio Coach | `/api/portfolio-coach/*` | Review queue approval/rejection for operator-mediated portfolio suggestions. |

## Route Inventory

Captured from `tradecraft.main.app.routes` on 2026-05-24. The import emitted a telemetry warning on stderr (`Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given`), but the route table was captured successfully.

| Area | Representative Routes | Auth Notes |
| --- | --- | --- |
| Public shell/docs | `/`, `/static`, `/openapi.json`, `/docs`, `/redoc` | Public framework/static surface. |
| Health | `/api/health` | Public readiness pointer; detailed ops readiness is protected. |
| Ops/settings/LLM usage/live authority | `/api/ops/readiness`, `/api/settings/catalog`, `/api/settings/values`, `/api/llm/status`, `/api/llm/probe`, `/api/llm/usage/summary`, `/api/llm/usage/status`, `/api/live/authority` | Admin protected. |
| Dashboard/market account | `/api/dashboard`, `/api/market/quotes`, `/api/market/account`, `/api/market/judgments/*`, `/api/market/pulse/*` | Admin protected except `/api/market/clock`. |
| Strategy/research helper | `/api/helper/ask`, `/api/research/ask`, `/api/strategy/insights/collect`, `/api/strategy/insights/{source_id}` | Admin protected. Some candidate/brief routes are currently public. |
| Strategy candidates | `/api/strategy/intent`, `/api/strategy/insights`, `/api/strategy/insights/signals`, `/api/strategy/candidates`, `/api/strategy/brief` | Captured as public routes; treat as review-needed because they can expose trading research context. |
| Symbol/discovery/ETF | `/api/symbols/*`, `/api/discovery/*`, `/api/etf/research/*` | Symbol fundamentals read is public; collection, analysis history, discovery, and ETF routes are admin protected. |
| Memory | `/api/memory/status`, `/api/memory/today`, `/api/memory/rituals/run-once`, `/api/memory/reflections/run-due`, `/api/memory/policies/*` | Admin protected. |
| Jue Wiki | `/api/wiki/status`, `/api/wiki/context`, `/api/wiki/search`, `/api/wiki/pages/{page_id}`, `/api/wiki/pages/{page_id}/sources`, `/api/wiki/application/status`, `/api/wiki/application/effectiveness`, `/api/wiki/lint/findings`, `/api/wiki/rebuild`, `/api/wiki/lint`, `/api/wiki/repair/run-once` | Status is public health metadata. Context/search/page/source/lint-finding/application reads and rebuild/lint/repair execution are admin protected because they expose compiled trading intelligence and outcome feedback. |
| KIS blocks | `/api/kis/blocks`, `/api/kis/blocks/status`, `/api/kis/blocks/manager/run-once`, `/api/kis/blocks/executor/tick`, `/api/kis/blocks/kill-switch`, `/api/kis/blocks/{block_id}/*` | Admin protected; active Korean account/block-trading controls. |
| KIS rebalance | `/api/rebalance/kis-status` | Admin protected; read-only rebalance/status compatibility surface backed by the KIS block trader. |
| Binance blocks/context | `/api/binance/blocks`, `/api/binance/blocks/status`, `/api/binance/blocks/manager/run-once`, `/api/binance/blocks/executor/tick`, `/api/binance/blocks/kill-switch`, `/api/binance/quant/signals`, `/api/binance/patterns/context` | Admin protected; these affect crypto spot/futures block operation or expose account/risk context. |
| Crypto research/alpha | `/api/crypto/research/*`, `/api/crypto/alpha/*` | Admin protected; feeds Binance 쥬 context. |
| Reports/RAG | `/api/reports/status`, `/api/reports/search`, `/api/rag/status`, `/api/rag/search`, `/api/reports/crawl-once`, `/api/reports/repair-metadata`, `/api/reports/backfill-symbol-links`, `/api/rag/sync` | Status/search are public; mutation/sync routes are admin protected. |
| Telegram | `/api/telegram/status`, `/api/telegram/webhook` | Route table shows public, but webhook validates configured secret and chat ID before command processing. |
| Runtime storage | `/api/runtime/storage`, `/api/runtime/storage/cleanup` | Status is public; cleanup is admin protected. |
| Portfolio coach | `/api/portfolio-coach/review-queue`, approval, rejection | Admin protected. |

## Protected Vs Public Notes

- `/api/kis/blocks*`, `/api/binance/blocks*`, `/api/crypto/*`, `/api/memory/*`, `/api/settings*`, `/api/llm/*`, and operational market/account routes are protected by `require_admin_auth`.
- `/api/live/authority` is protected by `require_admin_auth` because it exposes live trading authority state, evaluator DB paths, scorecards, and venue budget multipliers.
- `/api/reports/status`, `/api/reports/search`, `/api/rag/status`, and `/api/rag/search` are public in the captured route dependencies. If report/RAG content becomes sensitive account-specific context, this split needs a security review.
- `/api/wiki/status` is public health metadata. `/api/wiki/context`, `/api/wiki/search`, `/api/wiki/pages/{page_id}`, `/api/wiki/pages/{page_id}/sources`, and `/api/wiki/lint/findings` expose compiled trading interpretation, selected page context, source-reference metadata, and quality findings; these read routes are admin-token protected.
- `/api/strategy/candidates` and `/api/strategy/brief` are public in the captured dependency table even though they summarize active trading research. Keep this explicit during refactors.
- `/api/telegram/webhook` is intentionally not admin-token gated because Telegram supplies its own secret header, but chat ID and secret validation are hard gates.

## Jue Wiki API Details

Phase 2 wiki endpoints are intended to expose both the compiled page content and
the operational health of that content:

| Route | Method | Purpose |
| --- | --- | --- |
| `/api/wiki/status` | `GET` | Returns page counts, lint counts, stale-page counts, prompt pressure, and service status from the wiki DB. |
| `/api/wiki/context` | `GET` | Admin-protected. Returns compact context for a scope/symbol/page type using existing page packing. |
| `/api/wiki/search` | `GET` | Admin-protected. Searches indexed wiki pages by query, optional scope, and optional page type. |
| `/api/wiki/pages/{page_id}` | `GET` | Admin-protected. Reads one compiled Markdown page and metadata. |
| `/api/wiki/pages/{page_id}/sources` | `GET` | Admin-protected. Returns source references for a page so claims can be traced back to reports, RAG, block ledgers, or memory rows. |
| `/api/wiki/application/status` | `GET` | Admin-protected. Returns recent decision links, effect metric counts, degraded page count, and recent advisory mode recommendations for the Applied Intelligence Loop. |
| `/api/wiki/application/effectiveness` | `GET` | Admin-protected. Returns page-level effectiveness metrics, optionally filtered by scope, from `wiki_page_effectiveness`. |
| `/api/wiki/lint/findings` | `GET` | Admin-protected. Lists lint findings, usually filtered to open findings and optional scope. |
| `/api/wiki/rebuild` | `POST` | Admin-protected rebuild request for a scope, with optional force. |
| `/api/wiki/lint` | `POST` | Admin-protected lint execution for a scope. |
| `/api/wiki/repair/run-once` | `POST` | Admin-protected repair pass that turns open lint findings into repair actions or unresolved manual work. |

The API should preserve the source-of-truth boundary. Wiki search and page reads
return compiled interpretation. Source endpoints point back to durable evidence,
but the reports/RAG stores, block ledgers, live performance stores, and
`investment_memory.db` remain authoritative for underlying facts and outcomes.

See [appendix/api_route_inventory.md](appendix/api_route_inventory.md) for the full captured route table.
