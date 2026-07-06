# Refactor Roadmap

This roadmap keeps HERMES/쥬 recognizable as an active KIS and Binance block-trading system while reducing ambiguity in domain boundaries, context packets, observability, and performance accounting.

## Phase 1: Documentation Lock

- Finish this specbook.
- Verify route, DB, settings, runner, and UI inventories.
- Add missing operational caveats.

Acceptance criteria:

- `docs/spec` has no placeholders and all Markdown fences are balanced.
- Route inventory, DB catalog, runtime process list, and settings categories match current source.
- KIS existing-position adoption, Binance wallet adoption, failed-entry caveats, live-order gates, kill switches, and public/admin route surfaces are documented.
- Future agents can identify whether a change belongs to KIS, Binance, shared memory, research, UI, API, or runtime before editing code.

## Phase 2: Domain Boundary Cleanup

- Extract common block-ledger concepts where safe.
- Keep KIS/Binance venue-specific execution separate.
- Normalize performance attribution across venues.

Acceptance criteria:

- Shared concepts such as block status, manager run, rule tick, order attempt, event, adoption source, and reflection have common names.
- KIS-specific concerns remain KIS-specific: KRW accounting, Korean market schedule, KIS token/rate limits, KRX/Naver identity, tax/fee semantics, and domestic account readiness.
- Binance-specific concerns remain Binance-specific: spot/futures market, USDT accounting, precision/filter rules, leverage, funding, liquidation distance, wallet cost basis, and 24h scheduling.
- Existing API payloads stay backward-compatible or are versioned.

## Phase 3: Context Packet Standardization

- Standardize manager prompt context schemas.
- Standardize model/reasoning/timeout visibility.
- Standardize compact vs full UI/API payloads.
- Treat `src/tradecraft/jue` workflow packs as the canonical source for
  repeatable LLM judgment procedures.

Acceptance criteria:

- KIS 쥬 and Binance 쥬 context packets identify account state, block state, research, memory, market context, quant/context layers, safety gates, and stale/missing data with consistent field names.
- Model, reasoning effort, timeout, component name, and usage telemetry are visible in readiness or status payloads for each LLM-backed manager.
- Public/compact payloads redact large prompts and sensitive context consistently while preserving enough status for operators.
- Context packet tests cover missing research, stale runtime data, adopted blocks, and failed-entry records.
- Every LLM-backed manager or research/memory routine declares a workflow id,
  persists workflow provenance, and can be checked through
  `/api/jue/workflows/status`.

## Phase 4: Observability and Accounting

- Add first-class realized/unrealized PnL reports.
- Add wallet/existing-position exclusion switches.
- Add order-fill provenance displays.

Acceptance criteria:

- KIS and Binance performance reports can explicitly include or exclude `existing_position` and `wallet_adoption`.
- Failed entries, order rejects, canceled triggers, paper fills, exchange fills, open unrealized PnL, and closed realized PnL are separate categories.
- UI and API expose accounting source, fill source, fees/taxes/slippage/funding status, and confidence level for every performance row.
- `/api/ops/readiness`, block status endpoints, `.runtime/*.json`, `.runtime/*.log`, and `.runtime/llm_usage.db` remain the primary operator surfaces.

## Phase 5: Strategy/Quant Expansion

- Add KIS quant only after current gaps are documented and stabilized.
- Keep crypto quant separate from Korean-equity quant.
- Compare quant-derived decisions against realized block outcomes before increasing authority.

Acceptance criteria:

- Any new `kis_quant` layer has a separate DB, schema, context contract, retention policy, and evaluation loop.
- Crypto quant, pattern lab, and alpha services continue to feed Binance 쥬 without silently influencing KIS equity decisions.
- Quant features are scored against realized, fill-proven block outcomes with adoption and failed-entry exclusions available.
- Strategy authority changes are gated by sample size, venue-specific risk review, and regression tests.

## Refactor Acceptance Criteria

- Existing tests pass.
- Runtime DB migrations are explicit and reversible where possible.
- UI can still operate KIS and Binance blocks.
- Admin auth and safety gates remain intact.
- 쥬 memory/reflection provenance is not lost.

Before any refactor branch is considered complete, run focused API/block tests, verify the static UI still loads, inspect readiness output, and confirm no application code accidentally changes trading behavior while docs or boundaries are being adjusted.
