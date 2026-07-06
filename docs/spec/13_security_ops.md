# Security & Operations

## Security Model

- Local-first does not mean unauthenticated for trading APIs.
- Admin token gates operational mutation and account/order-sensitive reads.
- `require_admin_auth` accepts either `Authorization: Bearer <token>` or `X-TradeCraft-Admin-Token`; configured tokens come from `admin_tokens` and `admin_token`.
- Telegram commands are scoped to configured chat IDs and webhook secrets.
- The static UI stores the admin token only in `sessionStorage`, then sends both supported admin-token headers for protected API calls.
- HERMES/쥬 is an active trading partner system. Security controls must protect real KIS account actions, Binance spot/futures actions, block ledgers, strategy context, memory, and settings, not merely generic web data.

## Hard Safety Gates

- Kill switch.
- Cash/orderable amount check.
- Available quantity check.
- Duplicate order prevention.
- Exchange filter/precision/min-notional checks.
- Binance risk limits: futures leverage, liquidation-distance, account-risk, total exposure, symbol exposure, and reward/risk settings.
- KIS shared rate limiting and token reuse where implemented.
- Rate limiting/token reuse where implemented.
- Auth before operational endpoints.
- Live-order toggles before order submission: `kis_block_trader_execute_orders`, `binance_block_trader_execute_spot_orders`, and `binance_block_trader_execute_futures_orders`.
- Telegram webhook secret and chat ID checks before command execution.

## Operational Safety

- Live/paper state must be visible.
- Stale process and restart-needed states must be visible.
- Failed LLM calls must not generate fallback trades.
- Failed orders must be stored as error/expired, not hidden behind `sent`.
- Manager runs propose/adapt block intent; deterministic executors enforce target/stop/trigger/order checks.
- Existing holdings can be adopted into blocks without a buy order, but adoption must still be auditable in block events.
- Kill switches for KIS and Binance blocks must stay fast, visible, and reversible only through explicit release actions.
- Settings changes to active-trading toggles should be reflected in readiness and UI state before operators trust the system.
- Report/RAG repair, sync, and legacy migration paths must be treated as operational maintenance, not casual UI actions.

## Protected/Public Review Points

Captured route dependencies show the main operational block-trading routes as admin protected:

- `/api/kis/blocks*`
- `/api/binance/blocks*`
- `/api/crypto/*`
- `/api/memory/*`
- `/api/settings*`
- `/api/llm/usage/*`
- `/api/ops/readiness`
- `/api/dashboard`
- `/api/market/account`, `/api/market/quotes`, `/api/market/judgments/*`, `/api/market/pulse/*`

Captured public surfaces that deserve periodic review:

- `/api/strategy/candidates`, `/api/strategy/brief`, and some strategy insight reads expose research/trading context.
- `/api/reports/status`, `/api/reports/search`, `/api/rag/status`, and `/api/rag/search` expose report/RAG status or search results.
- `/api/runtime/storage` exposes storage summary while cleanup is protected.
- `/api/telegram/webhook` is public at HTTP auth level but protected by Telegram secret/chat validation.

## Security Refactor Risks

- UI convenience must not bypass admin auth.
- Reports/insights collection must not accept arbitrary file/path/URL inputs.
- Legacy pickle/RAG migration must remain explicitly gated.
- Secrets must stay in `.env` or local ignored config.
- Do not weaken route dependencies while moving handlers or splitting routers; route inventory should be regenerated after API refactors.
- Public strategy/report/RAG routes should be revisited if they begin including account-specific, position-specific, or block-specific context.
- Settings catalog must redact or mask secrets and preserve high-risk confirmation around live trading flags.
- Telegram shortcuts must not become a second unaudited trading API.
- KIS and Binance safety gates must remain venue-specific; crypto futures risk rules cannot be assumed to protect Korean equities, and KIS cash/quantity checks cannot protect Binance futures exposure.
