# Known Gaps

These are known weaknesses in the current HERMES/쥬 active trading system. They are not generic trading disclaimers; they are implementation and operations caveats that must stay visible during refactors and performance review.

## Trading Performance Interpretation

- Existing-position and wallet-adoption gains can inflate perceived 쥬 performance.
- Some failed-entry reflections may appear as loss-like records without real fills.
- Fees/taxes/slippage treatment needs explicit verification by venue.
- Live edge scorecards can be sparse; `observe_only` or `insufficient` authority
  should be treated as missing evidence, not as proof of no risk.

KIS `created_by='existing_position'` blocks and Binance `created_by='wallet_adoption'` blocks are active risk-management records, not clean proof that 쥬 generated the original entry alpha. They should be excluded or separately bucketed when judging 쥬-created block performance.

`live_block_performance` adds explicit inclusion flags for Jue alpha, risk management, and execution quality, but accounting exactness is not yet audit-grade. Snapshot PnL, estimated cost basis, reflection PnL, paper fills, exchange fills, taxes, fees, slippage, funding, and manual/admin outcomes need stricter provenance before performance can be treated as exact realized accounting.

## KIS Gaps

- KIS does not yet have a Binance-style dedicated `kis_quant` layer.
- KIS current quote snapshots are available, but technical indicators are not fully promoted into manager context.
- KIS symbol display can still degrade to code-only names when identity repair/context is incomplete.

KIS has market judgment, market pulse, Naver/RAG, suitability, ETF, valuation, and daily discovery context, but not a standalone quant repository equivalent to `.runtime/crypto_quant.db`. Adding `kis_quant` should wait until the current specbook and context boundaries are locked.

KIS accounting also needs more explicit fee/tax/slippage rules. Current services expose some KIS fee/tax fields, but block-level performance still needs a clear source-of-truth hierarchy for exact realized PnL.

## Binance Gaps

- Binance sample size is still tiny.
- Precision/filter handling must stay visible.
- Spot/futures PnL attribution needs strict wallet-adoption exclusion.

Binance has richer crypto-specific quant, pattern, research, and alpha support than KIS, but the performance sample is not large enough to increase strategy authority only from recent scorecards unless the live authority packet reaches `scale_candidate` with enough samples. Spot and futures share a runtime while having different risk semantics, fee/funding behavior, liquidation concerns, and exchange filters.

Wallet-adopted spot positions must be separated from LLM-created spot/futures blocks in every performance interpretation. Precision/filter rejects and failed entries are operational quality signals, not realized trading outcomes unless exchange fill evidence exists.

## UI Gaps

- Navigation has grown organically.
- Open block board and history views need clear separation.
- Long text and modal/detail navigation need continued readability checks.

The UI has many active operator surfaces: dashboard, investment helper tabs, runtime/settings, KIS blocks, Binance blocks, research, reports, memory, and strategy views. The route/public surfaces review in `11_api_reference.md` should stay visible because some research/status endpoints are public while block controls are admin protected.

The UI should continue making active trading identity explicit: KIS and Binance block status, live-order mode, paper mode, kill switches, adoption source, failed-entry caveats, and stale runner warnings should be obvious without reading logs.

## Documentation Gaps To Close Later

- Exact env aliases for every setting.
- Exact API request/response schemas for every endpoint.
- End-to-end sequence diagrams for every runner.
- Fee/tax/slippage accounting spec.

Additional documentation gaps:

- Route/public surfaces review for strategy, reports, RAG, runtime storage, Telegram, and helper endpoints.
- Names/code-only symbol identity repair examples for KIS, ETF, and report-derived candidates.
- Exact block accounting source taxonomy across exchange fills, paper fills, adopted positions, failed entries, and manual reflections.
- Acceptance tests that prove docs, UI labels, and API payloads all preserve adoption and failed-entry caveats.
