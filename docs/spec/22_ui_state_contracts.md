# UI State Contracts

The HERMES UI is a static frontend without a bundler. This document defines the
state, refresh, and interaction contracts that refactors must preserve.

## UI Files

| File | Responsibility |
| --- | --- |
| `src/tradecraft/web/static/index.html` | DOM structure, tab containers, modal roots, script/style version references. |
| `src/tradecraft/web/static/app.js` | Shared state, fetch helper, rendering, event handlers, refresh loops, tab persistence. |
| `src/tradecraft/web/static/style.css` | Theme tokens, layout, responsive behavior, cards/tables/modals/chips. |

## Design Direction

The current visual direction is an AI research-room and trading-operations UI:

- dark premium surface;
- readable research text;
- dense but scannable block boards;
- chips for source/coverage/status;
- visible stale/error/auth states;
- no marketing landing page;
- operator-first layout.

## Global State Contract

The frontend should keep one centralized state object. Refactors can split
rendering functions, but the following state concepts must remain represented:

- admin auth token status;
- current top-level tab;
- selected block/trading view;
- dashboard snapshot;
- KIS blocks/status/history;
- Binance blocks/status/history;
- live authority status and KIS/Binance authority packets;
- research and strategy packets;
- memory status/today/reviews/replays/policies;
- market pulse/judgment/account;
- settings catalog and pending edits;
- LLM usage/native status;
- system readiness and restart-required banners;
- last refresh timestamps and errors.

## Auth Contract

- Admin token is stored in `sessionStorage`, not localStorage or files.
- All protected API calls use the central fetch helper.
- 401/403 responses show an auth banner or token modal.
- Trading buttons must not fire unauthenticated calls.
- The UI should never display full secret values.

## Top-Level Navigation Contract

The operator mainly watches blocks. Refactors should prioritize:

1. Dashboard/overview.
2. KIS Jue blocks.
3. Binance Jue blocks.
4. Research.
5. Memory/Learning.
6. Settings/Ops.

Subtabs should be minimized when they duplicate top-level tabs. The current tab
should persist across refresh so a browser reload does not throw the operator
back to the wrong screen.

## Block Board Contract

Each active block card/row should show:

- symbol name first, code second;
- block id shortened but copyable/inspectable;
- venue/market/side;
- horizon;
- status;
- quantity open/initial;
- entry/current/target/stop;
- PnL with correct unit and decimals;
- next rule action;
- entry trigger if waiting;
- thesis/risk compact text;
- source/coverage/policy chips;
- action buttons gated by auth and block status.

Closed blocks should be available in history but not crowd the active board.
History should support date navigation and filtering by venue, symbol, status,
side, and created-by source.

## KIS UI Contract

KIS sections should show:

- official KIS account total and cash/deposit breakdown;
- open blocks;
- proposed/waiting-entry blocks;
- unallocated existing holdings;
- latest manager run;
- live authority grade, scorecard count, `max_budget_multiplier`, and
  scale-up allowance;
- rule tick and reconciliation status;
- market clock and market pulse;
- memory policy impacts;
- KIS research coverage and symbol-analysis status.

The UI must avoid confusing official account totals with estimated dashboard
totals. When both are shown, label them distinctly.

## Binance UI Contract

Binance sections should show:

- spot and futures readiness;
- spot/futures live execution toggles;
- active blocks split by market and side;
- proposed/waiting-entry blocks;
- wallet-adopted blocks;
- realized Jue block performance;
- open unrealized performance;
- live authority grade, scorecard count, `max_budget_multiplier`, and
  scale-up allowance;
- latest manager watch notes in Korean;
- crypto research/quant/pattern/alpha freshness;
- order/filter errors with enough detail to debug.

Small USDT PnL values must not round to `0` if decimals matter. Use decimals
appropriate to crypto size.

The Binance pattern/backtest surface is part of the crypto research top-level
page. It should render `optimized_strategy_sets` from
`/api/binance/patterns/context` as a structured backtest lab:

- summary KPIs for optimized sets, qualified scorecards, raw scorecards,
  pattern candidates, and latest promotion time;
- optimized set cards with stop/target/holding-bar parameters and audited
  objective/win/expectancy/profit-factor/trade-count metrics;
- raw scorecard rows with entry-quality pass/fail visibility;
- a short operator note that these sets are Jue price-geometry priors, while
  live authority, spread, funding, order book, and execution gates still decide
  whether a block can be created.

The Binance block board should additionally show a compact backtest live
confluence panel. That panel summarizes top optimized sets against the currently
loaded quant signal and Binance live authority so the operator can see why a
set is aligned, waiting, or contradicted without opening the full research page.

## Research UI Contract

Research UI should expose:

- report DB status;
- RAG status;
- valuation/fundamentals status;
- ETF research status;
- strategy insights status;
- daily discovery status;
- crypto research status;
- source freshness and errors;
- full-text/modal views for long summaries.

Research cards should include source identity and staleness. Long content should
open in a modal or detail panel instead of being truncated without access.

## Memory UI Contract

Memory UI should expose:

- persona;
- today's pre-open/midday/post-close journals;
- active policies and scorecards;
- recent reflections;
- weekly/monthly reviews;
- historical replays;
- policy revisions with activate/reject controls;
- symbol and block memory links.

The UI should show whether memory is empty, stale, or runner-stopped.

## Settings/Ops UI Contract

Settings/Ops should expose:

- model and reasoning settings;
- KIS/Binance execution toggles;
- runner intervals;
- universe limits;
- research settings;
- LLM usage status;
- Codex native account/model status;
- live evaluator state, DB paths, and `/api/live/authority` status;
- readiness;
- restart controls;
- system metrics.

High-risk settings must require explicit confirmation through the API contract.

## Refresh Contract

Suggested refresh behavior:

- readiness/system metrics/live authority: short interval, lightweight;
- block boards: active refresh without full page reload;
- research/memory: moderate interval unless user opens panel;
- settings catalog: load on demand or slow interval;
- history: manual refresh or date navigation.

Refresh errors should update the relevant panel and not reset tab selection.

## Frontend Verification

Minimum checks after UI changes:

```bash
node --check src/tradecraft/web/static/app.js
pytest tests/test_static_ui.py
```

For layout-sensitive changes, use browser/Playwright checks at:

- desktop 1440x1000;
- wide 2560x1440;
- mobile 390x844.

Check for horizontal overflow, broken cards, invisible text, clipped modals, and
tabs resetting unexpectedly.
