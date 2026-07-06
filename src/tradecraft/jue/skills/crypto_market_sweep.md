---
skill_id: crypto_market_sweep
name: Crypto Market Sweep
version: 1
scope: binance
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/agent-plugins/market-researcher/agents/market-researcher.md
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/sector-overview/SKILL.md
required_outputs:
  - market_regime
  - symbol_shortlist
  - spot_futures_choice
  - data_gaps
max_prompt_chars: 1700
---
# Crypto Market Sweep

Jue must scan Binance as a 24h market with separate spot and futures lanes. Start from liquid universe, quote volume, volatility, spread, funding, open interest, trend, reversal risk, and recent block outcomes.

For each candidate, decide:
- spot long, futures long, futures short, waiting-entry, or reject
- why this market lane is better than the other lane
- whether order book/spread is fresh enough
- whether recent churn argues for waiting instead of immediate entry
- which quant features matter most

Short candidates require futures availability and extra liquidation-distance awareness.
