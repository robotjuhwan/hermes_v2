---
skill_id: earnings_preview
name: Earnings Preview
version: 1
scope: kis
source_inspiration:
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/earnings-preview/SKILL.md
required_outputs:
  - event_setup
  - consensus_bridge
  - surprise_paths
  - block_plan
max_prompt_chars: 1500
---
# Earnings Preview

Jue prepares an earnings setup before the event enters the KIS decision loop. Tie the setup to current blocks, watch symbols, sector context, and available reports.

Include:
- event date, report window, and source freshness
- consensus revenue, profit, margin, and guidance inputs when available
- upside and downside surprise paths
- what price reaction would confirm or reject the current thesis
- whether to protect, wait, reduce exposure, or leave the block unchanged

Keep the preview short and separate known facts from scenario judgment.
