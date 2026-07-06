---
skill_id: idea_generation
name: Idea Generation
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/idea-generation/SKILL.md
required_outputs:
  - candidate_screen
  - hypothesis
  - reject_reason
  - data_gaps
max_prompt_chars: 1800
---
# Idea Generation

Jue must search beyond obvious movers. For every discovery pass, classify candidates by style: value, growth, quality, event, theme, pullback, breakout, sector rotation, ETF/core allocation, and contrarian recovery.

For each candidate, produce:
- symbol and name
- style bucket
- current catalyst or pattern
- why this could be mispriced now
- what would prove the idea wrong
- whether the right action is create-now block, waiting-entry block, watch note, or reject

Screens surface candidates, not conclusions. A candidate becomes a block only when block_design and risk_sizing can produce an executable structure.
