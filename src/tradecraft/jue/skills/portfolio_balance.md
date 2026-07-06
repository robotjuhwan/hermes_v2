---
skill_id: portfolio_balance
name: Portfolio Balance
version: 1
scope: shared
source_inspiration:
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/wealth-management/skills/portfolio-rebalance/SKILL.md
required_outputs:
  - allocation_state
  - drift_note
  - rebalance_action
  - cash_plan
max_prompt_chars: 1600
---
# Portfolio Balance

Jue manages the portfolio, not only isolated trades. Review cash, short/mid/long/core ETF balance, single-symbol concentration, sector concentration, and idle capital.

For KIS:
- use official account total value for scale
- use orderable cash for buy sizing
- treat user-bought positions as special-watch until thesis and horizon are assigned
- consider ETF/core blocks for market exposure and long-horizon balance

For Binance:
- separate spot cash, futures margin, open futures risk, and existing spot holdings
- compare spot long vs futures long/short exposure
- reduce churn when recent block outcomes show low edge
