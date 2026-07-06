# Trading Execution Contracts

This document defines the trading contracts for KIS Jue and Binance Jue. It is
intended to let a refactor move code without changing trading semantics.

## Common Block Concepts

| Concept | Required Meaning |
| --- | --- |
| Block | One independent trading thesis with its own quantity, entry, target, stop, horizon, and lifecycle. |
| Open exposure | Quantity that has actually been filled/reconciled or paper-opened. |
| Proposed exposure | A waiting-entry or planned block that has not entered the market. |
| Manager | Native Codex/Jue call that proposes structured intent. |
| Rule executor | Deterministic service tick that watches prices and executes eligible triggers. |
| Reconciliation | Process that compares pending orders and positions to exchange/account evidence. |
| Safety gate | Non-LLM rule that can reject or pause an action. |

## Canonical Block Statuses

| Status | Meaning | Can Send Entry? | Can Send Exit? |
| --- | --- | --- | --- |
| `proposed` | Planned or waiting-entry block. | Yes, if trigger/action valid. | No, unless converted/opened first. |
| `entry_pending` | Entry order sent or awaiting fill/reconciliation. | No duplicate entry. | Usually no; reconcile first. |
| `open` | Filled/reconciled block with active exposure. | No entry; may update target/stop. | Yes, if target/stop/force close valid. |
| `exit_pending` | Exit order sent or awaiting fill/reconciliation. | No. | No duplicate exit until stale/failed/canceled. |
| `closed` | No open quantity remains. | No. | No. |
| `paused` | Temporarily frozen by Jue/operator/system. | No automatic entry. | Only explicit safe close if allowed. |
| `error` | Frozen due to operational failure or unresolved state. | No. | Only after operator/system recovery path. |

## LLM Manager Output Contract

Managers may propose `create_blocks`, `update_blocks`, `close_blocks`,
`pause_blocks`, and adoption-specific actions where supported.

Managers must provide symbol and display name if available, quantity or sizing
rationale, horizon, thesis, reason/evidence, risk note, entry style, executable
price structure, target, stop, confidence, data gaps, and source references.

Managers must not assume an order filled without exchange or paper-mode
evidence; override kill switches; ignore cash, available quantity, min-notional,
or leverage limits; create a block without enough price structure for
deterministic execution; or block a new thesis solely because the same symbol
already exists.

## Waiting-Entry Contract

Waiting-entry blocks let Jue say, "buy/sell only if this price condition is
reached." They are critical because the rule executor can act between manager
runs.

Required metadata:

- `entry_style = wait_for_price`;
- `entry_trigger_price`;
- `entry_trigger_operator`, usually `<=` for pullback entries or `>=` for
  breakout entries;
- `entry_trigger_status = waiting`;
- `trigger_reason`;
- target/stop that remain valid relative to the eventual executable price.

Rule executor behavior:

1. Poll eligible symbols on the configured rule interval.
2. Compare current executable reference price to the trigger.
3. If triggered, refresh exchange quote/book evidence.
4. Revalidate cash/quantity/filters/tolerance.
5. Move to `entry_pending` and write an order record, or record a rejection.

The LLM is not required for the trigger tick. The LLM is required only for
creating/updating the waiting block and for later strategic review.

## KIS Execution Contract

KIS Jue handles Korean domestic long stock/ETF blocks. It does not handle short
selling, margin, futures, or derivatives in v1.

KIS session rules:

- Regular trading is KRX 09:00-15:30 KST.
- The manager cadence is configured by
  `TRADECRAFT_KIS_BLOCK_TRADER_MANAGER_INTERVAL_SEC` and defaults to 1800
  seconds.
- The rule executor cadence is configured by
  `TRADECRAFT_KIS_BLOCK_TRADER_RULE_INTERVAL_SEC` and defaults to 10 seconds.
- Late-session behavior may restrict new entries and prioritize risk
  management.

KIS order pricing:

- KIS entries and exits use aggressive limit prices.
- Entry reference is current quote price.
- Buy limit is rounded up by aggressive bps and tick-size rules.
- Sell limit is rounded down by aggressive bps and tick-size rules.
- Target/stop validation requires a coherent relationship to reference price.

Required KIS gates:

- Admin auth for API-triggered actions.
- KIS primary account readiness.
- Live execution toggle:
  `TRADECRAFT_KIS_BLOCK_TRADER_EXECUTE_ORDERS`.
- Kill switch/system state if active.
- Cash/deposit availability for buys.
- Available quantity for sells.
- No duplicate pending order for same block/action.
- Symbol must be a valid KRX code.
- Quote must be recent enough for the action.
- Reconciliation must confirm filled state before final status changes.

Existing-position adoption:

- Adoption never sends a buy order.
- Adoption uses unallocated quantity from account reconciliation.
- Adopted blocks get `created_by='existing_position'` or equivalent metadata.
- Jue can assign horizon, thesis, target, stop, and risk management plan.
- Adopted block performance must distinguish pre-adoption PnL from Jue-managed
  post-adoption performance.

## Binance Execution Contract

Binance Jue handles spot long blocks, futures long/short blocks where
configured, and wallet adoption for existing spot balances.

Cadence:

- Crypto market is 24h.
- Quote and rule ticks default to 15 seconds.
- Manager interval is configured by
  `TRADECRAFT_BINANCE_BLOCK_TRADER_MANAGER_INTERVAL_SEC`.
- Scheduled Telegram reports default to 06:00, 12:00, and 20:00 local slots.

Venue universe:

- Spot universe comes from `TRADECRAFT_BINANCE_BLOCK_TRADER_SPOT_UNIVERSE` plus
  research-ranked spot candidates.
- Futures universe comes from `TRADECRAFT_BINANCE_BLOCK_TRADER_FUTURES_UNIVERSE`
  plus eligible research-ranked futures candidates.
- Validation must reject a spot/futures action if the symbol is outside the
  current venue universe.

Order pricing:

- Immediate entries refresh book ticker before order submission.
- Spot buys use best ask plus aggressive buffer.
- Futures longs use best ask plus aggressive buffer.
- Futures shorts use best bid minus aggressive buffer.
- If the executable reference deviates too far from intended entry, keep or
  convert the block to waiting-entry instead of submitting a stale order.

Required Binance gates:

- API key readiness for the market being traded.
- Live execution toggles for spot and futures.
- Kill switch.
- Exchange filter availability where possible.
- Min-notional, step size, tick size, precision, and quote budget.
- Spot cannot short.
- Futures leverage must not exceed configured max.
- Futures liquidation distance must be acceptable.
- No duplicate pending order for same block/action.
- Rule tick and manager run must both leave durable event/order records.

## Performance Attribution Contract

Performance must distinguish Jue-created live blocks, Jue-created paper blocks,
adopted KIS existing holdings, adopted Binance wallet holdings, operational
failures before fill, operational failures after fill, open/unrealized PnL, and
closed/realized PnL.

Reflections should not label a pre-fill exchange filter error as a trading loss.
They should label it as an execution-quality failure.

## Error Handling Contract

| Error | Required Behavior |
| --- | --- |
| LLM timeout | Record manager run error; no synthetic action. |
| JSON parse error | Record error; do not create orders. |
| Missing price structure | Record rejected action or watch note; do not create an executable block. |
| Quote stale/missing | Skip action and record stale/missing evidence. |
| Exchange filter error | Record order/block event; freeze or keep waiting depending on fill evidence. |
| Reconciliation timeout | Mark pending order stale/error; block duplicate orders. |
| Kill switch | Reject automatic entry/exit unless explicit manual recovery path is implemented. |
| Auth missing | Return 401/403 at API layer; do not call exchange. |

## Tests Required For Execution Changes

- KIS: `tests/test_kis_block_trader.py`, `tests/test_kis_block_trader_runner.py`,
  `tests/test_kis_trader_api.py`, `tests/test_kis_adapter.py`.
- Binance: `tests/test_binance_block_trader.py`,
  `tests/test_binance_block_trader_runner.py`,
  `tests/test_binance_trader_api.py`, `tests/test_binance_adapter.py`,
  `tests/test_binance_risk.py`.
- Cross-cutting: `tests/test_investment_memory.py`,
  `tests/test_block_performance.py`, `tests/test_llm_usage.py`,
  `tests/test_codex_native.py`.
