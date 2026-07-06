# Databases

## Runtime DB Principles

Runtime SQLite files under `.runtime/*.db` are operational state, not a single normalized product database. Treat each file as the local ledger or cache for one runner/service boundary:

- Block ledgers are authoritative for block lifecycle state, rule events, manager decisions, and order attempts.
- Research and intelligence DBs are provenance stores for snapshots, scoring, prompts, responses, and generated notes.
- JSON columns preserve external payloads and LLM context. Refactors should type high-value fields gradually, not discard raw provenance.
- Time columns are stored as text or integer source timestamps depending on the upstream system. Preserve existing values during migrations.
- Most tables are append-friendly; primary-key snapshot tables keep the latest derived state while history tables preserve past observations.

All schema snapshots in this spec were captured from existing top-level runtime DB files when present. The local environment did not have the `sqlite3` CLI installed, so inspection used Python stdlib `sqlite3` in read-only mode against `sqlite_master` and `PRAGMA table_info`.

## Top-Level vs Nested Runtime Caveat

This task catalogs the top-level DBs named in the plan, such as `.runtime/kis_blocks.db` and `.runtime/naver_reports.db`. Nested runtime DBs also exist, for example `.runtime/naver_reports/reports.db`, `.runtime/marketdata/candles.db`, and `.runtime/rag_chroma/chroma.sqlite3`. Those nested files are implementation details or subsystem stores and should be documented separately before any refactor moves ownership boundaries.

## DB Catalog

| DB | Path | Main owner | Responsibility |
| --- | --- | --- | --- |
| `kis_blocks` | `.runtime/kis_blocks.db` | KIS block trader | Korean equity block ledger, manager runs, rule/order events, quotes, reconciliation state. |
| `binance_blocks` | `.runtime/binance_blocks.db` | Binance block trader | Binance spot/futures block ledger, manager runs, order attempts, kill switch, performance reflections. |
| `investment_memory` | `.runtime/investment_memory.db` | Investment memory runner | Reflections, memory runs, policy rules/outcomes, journals, symbol analyses, Telegram send records. |
| `jue_wiki` | `.runtime/jue_wiki/wiki.db` | Jue Wiki service | Page index, source references, rebuild runs, and lint findings for compiled Markdown wiki pages. |
| `live_performance` | `.runtime/live_performance.db` | Live evaluator | `live_block_performance` rows keyed by venue/block with attribution, cost-aware PnL, and inclusion flags. |
| `live_edge` | `.runtime/live_edge.db` | Live evaluator | `live_edge_scorecards` rows keyed by venue/strategy/evidence with empirical grades and authority multipliers. |
| `market_judgment` | `.runtime/market_judgment.db` | Market judge | Account snapshots, quote snapshots, LLM judgment runs, per-symbol stance records. |
| `market_pulse` | `.runtime/market_pulse.db` | Market pulse runner | Market regime/pulse snapshots with index, sector, block-alignment, risk flag, and gap payloads. |
| `naver_reports` | `.runtime/naver_reports.db` | Naver reports pipeline | Broker report metadata, chunks, extracted facts, symbol links, symbol directory. |
| `symbol_fundamentals` | `.runtime/symbol_fundamentals.db` | Symbol analysis/fundamentals | Valuation snapshots, financial snapshots, latest valuation scores. |
| `etf_research` | `.runtime/etf_research.db` | ETF research | ETF universe, market snapshots, liquidity/momentum/core-fit/risk scoring. |
| `llm_usage` | `.runtime/llm_usage.db` | LLM usage telemetry | Per-call component/model/status/latency/token accounting. |
| `crypto_market_research` | `.runtime/crypto_market_research.db` | Crypto market research | Crypto universe, klines, snapshots, derivatives, features, candidates, notes, research runs. |
| `crypto_quant` | `.runtime/crypto_quant.db` | Crypto quant | Current and historical quant signals plus labeled outcomes. |
| `crypto_pattern_lab` | `.runtime/crypto_pattern_lab.db` | Crypto pattern lab | Imported strategy sources, extracted patterns, OHLCV imports, backtest metrics, optimization runs/trials, optimized strategy sets. |
| `kr_equity_pattern_lab` | `.runtime/kr_equity_pattern_lab.db` | KR equity pattern lab | KIS live-alpha grouped train/test sets and venue-native optimized strategy sets for validation. |
| `crypto_alpha` | `.runtime/crypto_alpha.db` | Crypto alpha runner | Source snapshots, detected events, symbol links, event outcomes, hypotheses, context cache. |

## Live Performance And Edge DBs

`live_performance.db` is the attribution-aware performance store. Its
`live_block_performance` table records:

- `venue`, `block_id`, and `symbol`;
- attribution such as Jue-created, adopted existing position, adopted wallet
  position, unfilled/unrealized, or operational pre-fill failure;
- booleans for `include_in_jue_alpha`, `include_in_risk_management`, and
  `include_in_execution_quality`;
- `gross_pnl`, `net_pnl`, `cost_total`, `pnl_pct`, `filled`, source JSON, and
  `computed_at`.

`live_edge.db` is the empirical edge store. Its `live_edge_scorecards` table
records venue, strategy family, evidence key, sample count, expectancy, win
rate, rule-follow rate, execution-error rate, drawdown, grade, authority
multiplier, raw JSON, and `computed_at`. Grades currently include
`observe_only`, `insufficient`, `restricted`, `qualified`, and
`scale_candidate`.

These DBs are the source for `/api/live/authority`. They do not replace
KIS/Binance block ledgers, order reconciliation, exchange fills, memory
reflections, or tax/accounting audit data. They compress those outcomes into
manager-facing evidence about whether a venue/strategy/evidence lane may
observe, operate normally, restrict, or scale.

## Accounting Caveats

Performance accounting is not a unified realized PnL ledger yet:

- Existing/wallet adoption must be separated from new entries. A block can adopt an existing holding without sending a buy order, so initial exposure is not always proof of a filled entry in this system.
- Failed-entry reflections are decision/process outcomes, not realized exchange PnL, unless an entry order was actually filled. They can still be useful for learning but must not be mixed into realized trade performance.
- KIS and Binance use different quote currencies and account semantics. KIS records Korean equity quantities and KRW-oriented PnL fields, while Binance records USDT-oriented crypto spot/futures quantities, leverage, margin, and `pnl_usdt`.
- Reflections may be stored both in block ledgers and memory DBs. Deduplicate by `block_id` plus database/table origin, and include `source_run_id` where present, before producing aggregate reports.
- Live performance rows provide the normalized inclusion flags used for Jue alpha, risk-management, and execution-quality reporting. They still depend on correct source evidence from block ledgers and exchange/account reconciliation.
- Live edge scorecards are authority evidence, not realized PnL audit records. A `scale_candidate` grade can increase a manager budget multiplier only inside configured caps and only after hard execution gates pass.
- Snapshot prices and order responses are provenance, not a settlement system. Use exchange fills and account history for audit-grade realized PnL.

## Migration Notes For Refactors

- Start by preserving table ownership: do not merge KIS, Binance, memory, research, and telemetry stores until runner boundaries are explicit.
- Add migrations as forward-only, idempotent steps. Existing tables show additive evolution through appended columns.
- Keep raw JSON fields during normalization so historical manager prompts, responses, source payloads, and order responses remain auditable.
- Normalize time handling in new tables, but migrate old text/integer timestamp fields only with a clear compatibility layer.
- Add foreign-key-like relationships in application code first. Most cross-table relationships are implicit through shared identifiers such as `block_id`, `run_id`, `symbol`, `source_run_id`, and `event_id`; a few local foreign keys exist, but refactors should not assume comprehensive referential integrity.
- Separate "current state" tables from "history" tables in future schema names. Examples: `crypto_quant_signals` is current state, while `crypto_quant_signal_history` is append history.
- For block performance, keep an explicit field that marks accounting source: exchange fill, adopted wallet position, deterministic mark-to-market, failed entry, or manual/admin reflection.

## Appendix

See [DB Schema Snapshots](appendix/db_schema_snapshots.md) for concise table-level schema summaries.
