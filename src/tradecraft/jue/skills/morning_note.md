---
skill_id: morning_note
name: Morning Note
version: 1
scope: kis
source_inspiration:
  - https://raw.githubusercontent.com/anthropics/financial-services/main/plugins/vertical-plugins/equity-research/skills/morning-note/SKILL.md
required_outputs:
  - overnight_developments
  - key_events
  - trade_implications
  - risk_focus
max_prompt_chars: 1500
---
# Morning Note

Jue prepares a compact KIS trading-day note before active decisions. Summarize overnight market context, domestic catalysts, account pressure, open blocks, sector rotation, ETF/core allocation, and the specific risk focus for today.

Output should be short, opinionated, and tied to block implications:
- candidate to research
- block to protect
- block to avoid adding
- waiting-entry condition
- cash discipline reminder
