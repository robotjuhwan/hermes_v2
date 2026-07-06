---
skill_id: model_update
name: Model Update
version: 1
scope: kis
source_inspiration:
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/model-update/SKILL.md
required_outputs:
  - driver_changes
  - estimate_delta
  - valuation_delta
  - thesis_delta
max_prompt_chars: 1500
---
# Model Update

Jue updates a lightweight operating model when earnings, guidance, reports, or macro inputs change the assumptions behind a block or watch symbol.

Track:
- changed drivers and source timestamp
- revenue, margin, profit, cash-flow, or balance-sheet impact
- valuation range before and after the change
- sensitivity that matters most for the next decision
- thesis delta and confidence delta

Use ranges when precision is false. State which assumptions are measured, estimated, or still missing.
