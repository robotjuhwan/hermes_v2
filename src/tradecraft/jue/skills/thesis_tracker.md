---
skill_id: thesis_tracker
name: Thesis Tracker
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md
required_outputs:
  - thesis_statement
  - thesis_pillars
  - invalidation
  - conviction_delta
max_prompt_chars: 1700
---
# Thesis Tracker

Every active position, waiting block, and special-watch user holding needs a falsifiable thesis. Track what must happen for the block to work, what would weaken it, and which new data changed the view.

For each update, record:
- original thesis
- current evidence that strengthens it
- current evidence that weakens it
- catalysts still pending
- thesis impact: strengthen, neutral, weaken, broken
- action implication: hold, add, wait, tighten risk, loosen runner, close, or avoid new block

Do not call a trade good or bad only from PnL. Separate thesis quality, entry quality, execution quality, and market regime.
