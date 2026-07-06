# Binance 쥬

## Purpose

Binance 쥬 manages Binance spot and futures blocks using its own model, risk controls, crypto research, quant signals, and pattern context.

This is the HERMES/쥬 24h crypto trading surface. 쥬 decides block intent for
spot and futures, while venue gates enforce exchange filters, precision,
min-notional, isolated-margin futures policy, leverage limits, liquidation
distance, execution toggles, and the kill switch.

## Core Files

- `src/tradecraft/services/binance.py`
- `src/tradecraft/services/binance_block_trader.py`
- `src/tradecraft/services/binance_risk.py`
- `src/tradecraft/runtime/binance_block_trader_runner.py`
- `src/tradecraft/services/crypto_market_research.py`
- `src/tradecraft/services/crypto_quant.py`
- `src/tradecraft/services/crypto_pattern_lab.py`
- `src/tradecraft/services/crypto_alpha.py`

The configured Binance block manager model is in `src/tradecraft/config.py`:
`TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_MODEL` defaults to
`gpt-5.5`, and reasoning defaults to `xhigh`.

## Venue Split

| Market | Supported Direction | Notes |
| --- | --- | --- |
| Spot | Long | Uses spot balances, min-notional, filter constraints. |
| Futures | Long/Short where enabled | Uses leverage, futures filters, liquidation/risk checks. |

Spot and futures have independent live-execution toggles:
`TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_SPOT_ORDERS` and
`TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_FUTURES_ORDERS`. The manager prompt
passes both configured universes and a runtime `market_universe` expanded by
crypto research. Server validation rejects spot or futures blocks whose symbols
are outside the current venue universe.

## Binance-Specific Inputs

- Crypto market research.
- Crypto quant signal packets.
- Pattern lab scorecards and optimized strategy sets from imported strategies.
- Crypto alpha summaries.
- Spot/futures account and risk state.
- Binance-specific memory with scoped cross-venue lessons.
- Live authority packet for Binance grade, scorecards, `max_budget_multiplier`,
  and `allow_scale_up`.

The manager prompt includes normalized account snapshots, active/open blocks,
runtime spot/futures universe, external candidates, latest performance
scorecard, live authority packet, and scoped memory. Crypto research supplies
ranked liquid candidates and can expand the configured seed universes. Crypto
quant contributes compact long, short, no-trade, expected-R, and trend context.
Crypto alpha contributes event/catalyst memory, similar outcomes, lessons,
contradictions, and gaps. Pattern lab contributes strategy-derived scorecards and
parameterized `optimized_strategy_sets` with audited `stop_pct`, `target_pct`,
and `holding_bars` priors. These optimized sets can shape price geometry, but
they do not bypass live book checks, exchange filters, liquidation controls,
exposure caps, kill switch, or the live authority packet.

Each executable candidate can carry `calculated.pattern_live_crosscheck`. This
cross-check compares the optimized pattern prior with fresh book/spread data,
funding context, quant direction, and Binance live authority:

- `aligned`: Jue may consider the optimized stop/target/holding geometry.
- `wait`: prefer waiting-entry or hold until the missing/stale live condition
  improves.
- `contradicted`: avoid creating a new block unless Jue records an explicit
  override reason and every server-side safety gate still passes.

The Binance block screen shows a compact live confluence panel. The full
scorecard/optimization table remains on the crypto research page.

## Live Authority Usage

Binance manager prompts should consume the Binance venue packet from
`/api/live/authority` or the equivalent evaluator state. The packet is the
maximum evidence-backed authority for new or expanded spot/futures blocks:

- `observe_only` or `restricted`: keep new risk small, prefer waiting-entry
  designs or risk-reduction, and do not scale just because the LLM is confident.
- `insufficient`: allow exploration, but treat spot/futures edge as unproven.
- `qualified`: allow baseline budget sizing inside existing account, exposure,
  precision/filter, leverage, and liquidation-distance gates.
- `scale_candidate`: allow scaled sizing only up to `max_budget_multiplier` and
  only when `allow_scale_up=true`.

Spot and futures still need lane-specific risk interpretation. A Binance packet
can constrain the whole venue, while market/side performance scorecards and
entry-gate policy should still prevent futures losses from being hidden by spot
wins, or spot opportunities from inheriting futures authority. The packet does
not change `TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_SPOT_ORDERS`,
`TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_FUTURES_ORDERS`, exchange filters,
kill switch, leverage caps, liquidation-distance checks, or exposure limits.

## Entry Execution

Immediate-entry blocks re-check the Binance book ticker before submitting an
order. Spot buys use the current best ask, futures shorts use the current best
bid, and the aggressive limit buffer is applied to that fresh executable
reference rather than only to the LLM's intended entry price. If the executable
reference is outside the configured entry tolerance, the block remains a
waiting-entry `proposed` block instead of becoming an error.

Waiting-entry blocks are represented as `proposed` blocks with
`metadata.entry_style=wait_for_price`, `metadata.entry_trigger_price`, and
`metadata.entry_trigger_operator`. The Binance rule tick can trigger those
entries without another LLM manager run.

## Performance Accounting Rules

- Wallet adoption is not 쥬-created alpha.
- Failed precision/filter entries are operational failures, not realized trading losses unless filled.
- Realized performance should be based on filled entry/exit blocks.
- Open performance must be marked unrealized.
- Prefer `live_block_performance.include_in_jue_alpha` for aggregate
  Jue-alpha reporting when evaluator rows exist.

Spot wallet adoption is handled by `run_spot_adoption_once` and writes
`created_by='wallet_adoption'` with Binance spot wallet provenance. Those blocks
are useful for active management and risk visibility, but their pre-adoption PnL
must be excluded from 쥬-created alpha. Performance reflections live in
`block_performance_reflections` and are USDT based; attribution must preserve
market, side, fill status, entry/exit evidence, and whether the block was live,
paper, or wallet-adopted.

Failed precision, lot-size, price-tick, and min-notional entries are recorded as
order/block operational failures when they happen before a fill. They should not
be scored as realized trading losses unless exchange evidence shows an entry
filled and later exited.

## Known Operational Risks

- Precision and lot-size filter errors.
- IOC aggressive limit expiry.
- LLM timeout.
- Overlarge prompt/context packets.
- 24h cadence causing excessive calls without rate/usage controls.
- Pattern optimizer overfit if a parameter set is promoted from too little data
  or without live-result confirmation.

Additional Binance-specific risk points: futures blocks require isolated margin,
configured maximum leverage, and liquidation distance inputs; spot short blocks
are rejected; futures live readiness requires entry and liquidation evidence.
Exchange-filter fetch failures can leave normalization less precise, so
precision/filter errors must remain visible in manager runs, orders, and block
events.
