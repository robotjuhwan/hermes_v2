---
skill_id: sector_overview
name: Sector Overview
version: 1
scope: kis
source_inspiration:
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/sector-overview/SKILL.md
required_outputs:
  - sector_state
  - leader_laggard_map
  - rotation_signal
  - watchlist_implications
max_prompt_chars: 1500
---
# Sector Overview

Jue reviews Korean market sector pressure before idea generation or rebalance work. Focus on observable rotation, earnings revisions, catalysts, and portfolio exposure.

Produce:
- sector state: improving, stable, weakening, crowded, or stressed
- leaders, laggards, and ETF/core proxies
- key catalysts and data gaps
- current portfolio exposure and concentration risk
- symbols that deserve deeper work and symbols to avoid adding

Prefer a compact comparison table when multiple sectors compete for attention.
