---
skill_id: earnings_analysis
name: Earnings Analysis
version: 1
scope: kis
source_inspiration:
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/earnings-analysis/SKILL.md
required_outputs:
  - reported_vs_expected
  - guidance_delta
  - thesis_impact
  - follow_up_actions
max_prompt_chars: 1500
---
# Earnings Analysis

Jue analyzes a reported earnings event after numbers, guidance, price reaction, and report commentary are available. The output should update the live decision lifecycle, not become a long note.

Cover:
- reported figures versus prior expectation
- guidance change and management tone
- segment, margin, backlog, or cash-flow detail that matters for the thesis
- market reaction versus surprise direction
- thesis status: strengthened, unchanged, challenged, or broken
- follow-up data needed before any block change

Preserve uncertainty when data is partial or delayed.
