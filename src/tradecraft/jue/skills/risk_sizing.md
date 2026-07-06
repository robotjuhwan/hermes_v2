---
skill_id: risk_sizing
name: Risk Sizing
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/wealth-management/skills/portfolio-rebalance/SKILL.md
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/private-equity/skills/returns-analysis/SKILL.md
required_outputs:
  - size_reason
  - risk_budget
  - exposure_check
  - rejection_reason
max_prompt_chars: 1600
---
# Risk Sizing

Jue sizes blocks from account pressure, horizon, confidence, evidence quality, volatility, stop distance, concentration, and current open risk. Quantity is a decision output, not a fixed one-share habit.

Risk sizing must check:
- available cash or orderable balance
- unallocated position quantity for adoption/sell decisions
- exposure by symbol
- exposure by horizon
- stop distance and expected reward/risk
- liquidity and spread
- recent loss cluster or churn
- whether ETF/core exposure is a better vehicle than a single name

When the correct size is zero, say why. When exploratory size is used, label it as exploratory and define what would justify increasing it.
